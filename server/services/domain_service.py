"""Domain layer — per-domain sending budget, rate limiting, and health.

A single sending domain is shared by every account, so the domain is the
system's most valuable resource:

- ``DOMAIN_DAILY_BUDGET``     caps total sends per domain per day (100k default).
- ``DOMAIN_MINUTE_BUDGET``    smooths bursts (default: budget spread over ~8h).
- Health circuit breaker      throttles the whole domain at the daily bounce
                              cutoff or when complaint rates cross thresholds.

Domain daily quota is keyed by UTC date (one fleet-wide budget per domain).
Per-inbox quotas are keyed by worker timezone date elsewhere (queue_service).
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Set

from redis import Redis
from services.redis_client import get_redis_client

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# ── Config ─────────────────────────────────────────────────────────────────
DOMAIN_DAILY_BUDGET = int(os.getenv("DOMAIN_DAILY_BUDGET", "100000"))
DOMAIN_MINUTE_BUDGET = int(
    os.getenv(
        "DOMAIN_MINUTE_BUDGET",
        str(max(1, int(DOMAIN_DAILY_BUDGET / 480))),  # ~8h sending window
    )
)
DOMAIN_DAILY_BOUNCE_LIMIT = int(os.getenv("DOMAIN_DAILY_BOUNCE_LIMIT", "50"))
DOMAIN_COMPLAINT_RATE_MAX = float(os.getenv("DOMAIN_COMPLAINT_RATE_MAX", "0.01"))
DOMAIN_THROTTLE_TTL = int(os.getenv("DOMAIN_THROTTLE_TTL_SECONDS", "86400"))
DOMAIN_QUOTA_TTL = 172800  # 2 days

DOMAIN_MIN_SAMPLE_FOR_HEALTH = int(os.getenv("DOMAIN_MIN_SAMPLE_FOR_HEALTH", "500"))

# ── Lua scripts ─────────────────────────────────────────────────────────────

_RESERVE_DAILY_LUA = """
local key = KEYS[1]
local cap = tonumber(ARGV[1])
local n   = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3]) or 172800

if cap <= 0 then
    return n  -- unlimited
end
local current = tonumber(redis.call('GET', key) or '0')
if current + n <= cap then
    redis.call('INCRBY', key, n)
    redis.call('EXPIRE', key, ttl)
    return n
end
return 0
"""

_ACQUIRE_SLOT_LUA = """
local key = KEYS[1]
local cap = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2]) or 120

if cap <= 0 then
    return 1
end
local current = redis.call('INCR', key)
if current > cap then
    redis.call('DECR', key)
    return 0
end
redis.call('EXPIRE', key, ttl)
return 1
"""

# ── Key builders ────────────────────────────────────────────────────────────


def utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _utc_minute() -> int:
    return int(time.time() // 60)


def quota_key(domain: str, day: str) -> str:
    return f"domain_quota:{domain}:{day}"


def rate_key(domain: str, minute: int) -> str:
    return f"domain_rate:{domain}:{minute}"


def stats_key(domain: str, day: str) -> str:
    return f"domain_stats:{domain}:{day}"


def bounce_events_key(domain: str, day: str) -> str:
    return f"domain_bounce_events:{domain}:{day}"


def throttle_key(domain: str, day: Optional[str] = None) -> str:
    """Return the day-scoped health throttle key for ``domain``."""
    return f"domain_health:{domain}:{day or utc_date()}:throttled"


def extract_domain(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[-1].strip().lower() or None


def _redis_client() -> Redis:
    return get_redis_client(REDIS_URL)


# ── Daily budget ────────────────────────────────────────────────────────────


def reserve_daily(domain: str, n: int = 1, budget: Optional[int] = None) -> int:
    """Reserve ``n`` sends against the domain's daily budget.

    Returns ``n`` when reserved, ``0`` when the budget is exhausted.
    """
    cap = DOMAIN_DAILY_BUDGET if budget is None else int(budget)
    if cap <= 0:
        return n
    r = _redis_client()
    script = r.register_script(_RESERVE_DAILY_LUA)
    try:
        return int(
            script(
                keys=[quota_key(domain, utc_date())],
                args=[cap, n, DOMAIN_QUOTA_TTL],
            )
            or 0
        )
    except Exception:
        logger.warning("reserve_daily failed for %s", domain, exc_info=True)
        return n  # fail-open so sending is never blocked by Redis errors


def release_daily(domain: str, day: Optional[str] = None, n: int = 1) -> None:
    """Release previously reserved domain budget (e.g. failed sends)."""
    try:
        r = _redis_client()
        key = quota_key(domain, day or utc_date())
        current = int(r.get(key) or 0)
        r.decrby(key, min(n, current))
    except Exception:
        pass


# ── Send-slot rate limiting (per-minute burst smoothing) ───────────────────


def acquire_send_slot(domain: Optional[str], timeout_seconds: float = 120.0) -> bool:
    """Wait (up to ``timeout_seconds``) for a per-minute send slot.

    Replaces the old global send throttle: total throughput is governed by
    the domain budget and grows with the budget config instead of being
    pinned to one fixed interval.
    """
    if not domain or DOMAIN_MINUTE_BUDGET <= 0:
        return True
    r = _redis_client()
    script = r.register_script(_ACQUIRE_SLOT_LUA)
    deadline = time.time() + timeout_seconds
    while True:
        try:
            granted = int(
                script(keys=[rate_key(domain, _utc_minute())], args=[DOMAIN_MINUTE_BUDGET, 120])
                or 0
            )
            if granted:
                return True
        except Exception:
            return True  # fail-open
        if time.time() >= deadline:
            return False
        time.sleep(0.2)


# ── Health circuit breaker ──────────────────────────────────────────────────


def is_throttled(domain: Optional[str]) -> bool:
    if not domain:
        return False
    try:
        return _redis_client().get(throttle_key(domain)) is not None
    except Exception:
        return False


def get_throttle_reason(domain: Optional[str]) -> Optional[str]:
    """Return today's circuit-breaker reason, if the domain is throttled."""
    if not domain:
        return None
    try:
        return _redis_client().get(throttle_key(domain))
    except Exception:
        return None


def set_throttled(domain: str, reason: str, ttl: Optional[int] = None) -> None:
    try:
        _redis_client().setex(throttle_key(domain), ttl or DOMAIN_THROTTLE_TTL, reason or "1")
        logger.warning(f"Domain {domain} throttled: {reason}")
    except Exception:
        pass


def clear_throttled(domain: str) -> None:
    try:
        # Delete today's key and the pre-day-scoping legacy key.
        _redis_client().delete(
            throttle_key(domain),
            f"domain_health:{domain}:throttled",
        )
    except Exception:
        pass


def record_sent(domain: Optional[str]) -> None:
    if not domain:
        return
    try:
        r = _redis_client()
        key = stats_key(domain, utc_date())
        pipe = r.pipeline()
        pipe.hincrby(key, "sent", 1)
        pipe.expire(key, DOMAIN_QUOTA_TTL)
        pipe.execute()
    except Exception:
        pass


def record_bounce(domain: Optional[str], event_id: Optional[str] = None) -> None:
    if not domain:
        return
    try:
        r = _redis_client()
        day = utc_date()
        if event_id:
            dedupe_key = bounce_events_key(domain, day)
            is_new = bool(r.sadd(dedupe_key, event_id))
            r.expire(dedupe_key, DOMAIN_QUOTA_TTL)
            if not is_new:
                return

        key = stats_key(domain, day)
        pipe = r.pipeline()
        pipe.hincrby(key, "bounced", 1)
        pipe.expire(key, DOMAIN_QUOTA_TTL)
        pipe.execute()
    except Exception:
        pass


def record_complaint(domain: Optional[str]) -> None:
    if not domain:
        return
    try:
        r = _redis_client()
        key = stats_key(domain, utc_date())
        pipe = r.pipeline()
        pipe.hincrby(key, "complained", 1)
        pipe.expire(key, DOMAIN_QUOTA_TTL)
        pipe.execute()
    except Exception:
        pass


def evaluate_domain_health(domain: str) -> bool:
    """Evaluate today's absolute bounce limit and complaint rate.

    Returns True when the domain is (now) throttled.
    """
    r = _redis_client()
    stats = r.hgetall(stats_key(domain, utc_date()))
    sent = int(stats.get("sent", 0) or 0)
    bounced = int(stats.get("bounced", 0) or 0)
    complained = int(stats.get("complained", 0) or 0)
    complaint_rate = complained / sent if sent else 0.0

    if DOMAIN_DAILY_BOUNCE_LIMIT > 0 and bounced >= DOMAIN_DAILY_BOUNCE_LIMIT:
        set_throttled(
            domain,
            f"daily bounce limit reached ({bounced}/{DOMAIN_DAILY_BOUNCE_LIMIT}); sent={sent}",
        )
        return True

    if sent >= DOMAIN_MIN_SAMPLE_FOR_HEALTH and complaint_rate > DOMAIN_COMPLAINT_RATE_MAX:
        set_throttled(
            domain,
            f"complaint rate {complaint_rate:.2%} exceeds {DOMAIN_COMPLAINT_RATE_MAX:.2%}; "
            f"complaints={complained} sent={sent}",
        )
        return True

    clear_throttled(domain)
    return False


def get_active_domains() -> Set[str]:
    """Domains with stats recorded today."""
    domains: Set[str] = set()
    try:
        today = utc_date()
        for key in _redis_client().scan_iter(match=f"domain_stats:*:{today}"):
            parts = key.split(":")
            if len(parts) >= 3:
                domains.add(parts[1])
    except Exception:
        pass
    return domains


def get_fleet_usage() -> Dict[str, int]:
    """Fleet-wide (all domains) daily budget usage: {budget, used_today, remaining}."""
    budget = DOMAIN_DAILY_BUDGET
    used = 0
    try:
        today = utc_date()
        r = _redis_client()
        for key in r.scan_iter(match=f"domain_quota:*:{today}"):
            try:
                used += int(r.get(key) or 0)
            except (TypeError, ValueError):
                continue
    except Exception:
        pass
    return {
        "budget": budget,
        "used_today": used,
        "remaining": max(0, budget - used),
    }
