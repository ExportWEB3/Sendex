#!/usr/bin/env python3
"""
Worker alert monitor daemon.

Watches worker heartbeat/status keys in Redis and sends high-quality Telegram
incident and recovery notifications when workers go offline or stale.

This monitor is intentionally isolated from the email sending engine to avoid
impacting campaign/worker execution paths.
"""

import hashlib
import html
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import redis
from dotenv import load_dotenv


LOGGER = logging.getLogger("worker_alert_monitor")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


# Load local env first, then production env overrides (if present).
load_dotenv()
load_dotenv("/root/email-sender/server/env/.env.production", override=True)

MONITOR_REDIS_URL = os.getenv("WORKER_ALERT_REDIS_URL") or os.getenv("REDIS_URL", "redis://localhost:6379")


def detect_redis_db_index(redis_url: str) -> int:
    try:
        parsed = urlparse(redis_url)
        path = (parsed.path or "").strip("/")
        if path and path.split("/")[0].isdigit():
            return int(path.split("/")[0])

        query_db = parse_qs(parsed.query).get("db", [None])[0]
        if query_db is not None and str(query_db).isdigit():
            return int(query_db)
    except Exception:
        pass
    return 0


RUNBOOK_REDIS_DB = detect_redis_db_index(MONITOR_REDIS_URL)

TELEGRAM_ALERTS_ENABLED = os.getenv("TELEGRAM_ALERTS_ENABLED", "false").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_MESSAGE_THREAD_ID = os.getenv("TELEGRAM_MESSAGE_THREAD_ID", "").strip()

ALERT_ENV_NAME = os.getenv("ALERT_ENV_NAME", os.getenv("ENVIRONMENT", "production")).strip()
ALERT_MONITOR_INTERVAL_SECONDS = int(os.getenv("ALERT_MONITOR_INTERVAL_SECONDS", "20"))
ALERT_HEARTBEAT_STALE_SECONDS = int(os.getenv("ALERT_HEARTBEAT_STALE_SECONDS", "25"))
ALERT_WORKER_STATUS_STALE_SECONDS = int(os.getenv("ALERT_WORKER_STATUS_STALE_SECONDS", "360"))
ALERT_MIN_REPEAT_SECONDS = int(os.getenv("ALERT_MIN_REPEAT_SECONDS", "900"))
ALERT_RECOVERY_ENABLED = os.getenv("ALERT_RECOVERY_ENABLED", "true").lower() == "true"

ALERT_BASE_URL = os.getenv("BASE_URL", "https://metismachine.com")

HEARTBEAT_KEY = "worker:heartbeat"
WORKER_KEYS = [
    ("US/Eastern", "worker:us_eastern:status"),
    ("US/Pacific", "worker:us_pacific:status"),
]

STATE_KEY = "alerts:worker_monitor:state"
LAST_SENT_KEY = "alerts:worker_monitor:last_sent_ts"
DOWN_SINCE_KEY = "alerts:worker_monitor:down_since"
FINGERPRINT_KEY = "alerts:worker_monitor:fingerprint"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    value = ts.strip()
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def format_age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "n/a"
    sec = int(max(0, seconds))
    minutes, s = divmod(sec, 60)
    hours, m = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {m}m {s}s"
    if minutes > 0:
        return f"{minutes}m {s}s"
    return f"{s}s"


def safe_status(value: Optional[str]) -> str:
    if not value:
        return "unknown"
    return str(value).strip().lower() or "unknown"


def telegram_enabled() -> Tuple[bool, str]:
    if not TELEGRAM_ALERTS_ENABLED:
        return False, "TELEGRAM_ALERTS_ENABLED is false"
    if not TELEGRAM_BOT_TOKEN:
        return False, "TELEGRAM_BOT_TOKEN is empty"
    if not TELEGRAM_CHAT_ID:
        return False, "TELEGRAM_CHAT_ID is empty"
    return True, "ok"


def send_telegram_html(message_html: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if TELEGRAM_MESSAGE_THREAD_ID:
        payload["message_thread_id"] = TELEGRAM_MESSAGE_THREAD_ID

    request = Request(
        url,
        data=urlencode(payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8", errors="ignore")
        if '"ok":true' not in body:
            LOGGER.error("Telegram API non-ok response: %s", body)
            return False
        return True
    except Exception as exc:
        LOGGER.error("Failed to send Telegram alert: %s", exc)
        return False


def load_json_or_none(raw: Optional[str]) -> Optional[Dict]:
    if not raw:
        return None
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
        return None
    except Exception:
        return None


def evaluate_state(client: redis.Redis) -> Dict:
    now = now_utc()
    issues: List[str] = []
    workers: List[Dict] = []

    heartbeat_raw = client.get(HEARTBEAT_KEY)
    heartbeat_dt = parse_iso(heartbeat_raw)
    heartbeat_age = None

    if heartbeat_raw is None:
        issues.append("Global worker heartbeat is missing.")
    elif heartbeat_dt is None:
        issues.append("Global worker heartbeat timestamp is invalid.")
    else:
        heartbeat_age = (now - heartbeat_dt).total_seconds()
        if heartbeat_age > ALERT_HEARTBEAT_STALE_SECONDS:
            issues.append(
                f"Global worker heartbeat is stale ({int(heartbeat_age)}s old, threshold {ALERT_HEARTBEAT_STALE_SECONDS}s)."
            )

    for label, key in WORKER_KEYS:
        raw = client.get(key)
        info = load_json_or_none(raw)

        record = {
            "label": label,
            "redis_key": key,
            "status": "offline",
            "business_hours": "unknown",
            "updated_at": None,
            "updated_age": None,
            "local_time": "unknown",
        }

        if info is None:
            issues.append(f"{label} worker status key is missing or malformed.")
            workers.append(record)
            continue

        record["status"] = safe_status(info.get("status"))
        record["business_hours"] = str(info.get("business_hours", "unknown")).lower()
        record["local_time"] = str(info.get("local_time") or "unknown")

        updated_dt = parse_iso(info.get("updated_at"))
        if updated_dt is None:
            issues.append(f"{label} worker status has invalid updated_at.")
        else:
            age = (now - updated_dt).total_seconds()
            record["updated_at"] = utc_iso(updated_dt)
            record["updated_age"] = age
            if age > ALERT_WORKER_STATUS_STALE_SECONDS:
                issues.append(
                    f"{label} worker status is stale ({int(age)}s old, threshold {ALERT_WORKER_STATUS_STALE_SECONDS}s)."
                )

        workers.append(record)

    fingerprint_src = "|".join(sorted(issues)) if issues else "healthy"
    fingerprint = hashlib.sha1(fingerprint_src.encode("utf-8")).hexdigest()

    return {
        "now": now,
        "healthy": len(issues) == 0,
        "issues": issues,
        "workers": workers,
        "heartbeat_raw": heartbeat_raw,
        "heartbeat_dt": heartbeat_dt,
        "heartbeat_age": heartbeat_age,
        "fingerprint": fingerprint,
    }


def worker_matrix(snapshot: Dict) -> str:
    headers = ["Worker", "Status", "BizHours", "UpdatedAge", "UpdatedAt(UTC)"]
    rows = []

    for w in snapshot["workers"]:
        rows.append(
            [
                w["label"],
                w["status"],
                w["business_hours"],
                format_age(w["updated_age"]),
                w["updated_at"] or "n/a",
            ]
        )

    rows.append(
        [
            "GlobalHeartbeat",
            "alive" if snapshot["heartbeat_dt"] else "missing",
            "n/a",
            format_age(snapshot["heartbeat_age"]),
            utc_iso(snapshot["heartbeat_dt"]) if snapshot["heartbeat_dt"] else "n/a",
        ]
    )

    widths = [len(h) for h in headers]
    for row in rows:
        for i, col in enumerate(row):
            widths[i] = max(widths[i], len(str(col)))

    def pad(cols: List[str]) -> str:
        return " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(cols))

    divider = "-+-".join("-" * w for w in widths)
    lines = [pad(headers), divider]
    for row in rows:
        lines.append(pad(row))

    return "\n".join(lines)


def render_incident(snapshot: Dict, down_since: str, update_mode: bool) -> str:
    now_text = utc_iso(snapshot["now"])
    down_since_dt = parse_iso(down_since)
    duration = "unknown"
    if down_since_dt is not None:
        duration = format_age((snapshot["now"] - down_since_dt).total_seconds())

    issue_lines = "\n".join(f"- {html.escape(issue)}" for issue in snapshot["issues"]) or "- none"

    headline = "WORKER INCIDENT UPDATE" if update_mode else "WORKER INCIDENT OPENED"
    table = html.escape(worker_matrix(snapshot))

    message = (
        f"<b>FLEETCTRL-X Reliability Monitor</b>\n"
        f"<b>{headline}</b>\n\n"
        f"<b>Severity:</b> CRITICAL\n"
        f"<b>Environment:</b> {html.escape(ALERT_ENV_NAME)}\n"
        f"<b>Detected At (UTC):</b> {html.escape(now_text)}\n"
        f"<b>Down Since (UTC):</b> {html.escape(down_since)}\n"
        f"<b>Active Duration:</b> {html.escape(duration)}\n"
        f"<b>Impact:</b> Worker automation reliability is degraded; queue processing and reply handling may stall.\n\n"
        f"<b>Detection Summary</b>\n"
        f"{issue_lines}\n\n"
        f"<b>Live Worker Matrix</b>\n"
        f"<pre>{table}</pre>\n"
        f"<b>Immediate Runbook</b>\n"
        f"1) systemctl status emailsender-worker-eastern.service\n"
        f"2) systemctl status emailsender-worker-pacific.service\n"
        f"3) redis-cli -n {RUNBOOK_REDIS_DB} --scan --pattern 'worker:*'\n"
        f"4) journalctl -u emailsender-worker-eastern.service -u emailsender-worker-pacific.service -n 120 --no-pager\n\n"
        f"<b>Dashboard:</b> {html.escape(ALERT_BASE_URL)}"
    )
    return message


def render_recovery(snapshot: Dict, down_since: Optional[str]) -> str:
    now_text = utc_iso(snapshot["now"])
    duration = "unknown"
    if down_since:
        start = parse_iso(down_since)
        if start is not None:
            duration = format_age((snapshot["now"] - start).total_seconds())

    table = html.escape(worker_matrix(snapshot))

    message = (
        "<b>FLEETCTRL-X Reliability Monitor</b>\n"
        "<b>WORKER RECOVERY CONFIRMED</b>\n\n"
        f"<b>Environment:</b> {html.escape(ALERT_ENV_NAME)}\n"
        f"<b>Recovered At (UTC):</b> {html.escape(now_text)}\n"
        f"<b>Previous Incident Duration:</b> {html.escape(duration)}\n"
        "<b>Status:</b> All worker liveness checks are healthy again.\n\n"
        "<b>Live Worker Matrix</b>\n"
        f"<pre>{table}</pre>\n"
        f"<b>Dashboard:</b> {html.escape(ALERT_BASE_URL)}"
    )
    return message


def should_repeat(client: redis.Redis, now_ts: float) -> bool:
    raw = client.get(LAST_SENT_KEY)
    if not raw:
        return True
    try:
        last = float(raw)
    except Exception:
        return True
    return (now_ts - last) >= ALERT_MIN_REPEAT_SECONDS


def set_last_sent(client: redis.Redis, now_ts: float) -> None:
    client.set(LAST_SENT_KEY, str(now_ts))


def monitor_loop() -> None:
    redis_client = redis.from_url(MONITOR_REDIS_URL, decode_responses=True)
    last_inactive_reason = None

    while True:
        try:
            enabled, reason = telegram_enabled()
            if not enabled:
                if reason != last_inactive_reason:
                    LOGGER.info("Telegram alert monitor inactive: %s", reason)
                    last_inactive_reason = reason
                time.sleep(max(ALERT_MONITOR_INTERVAL_SECONDS, 30))
                continue

            last_inactive_reason = None

            snapshot = evaluate_state(redis_client)
            now_ts = snapshot["now"].timestamp()

            state = redis_client.get(STATE_KEY) or "healthy"
            last_fingerprint = redis_client.get(FINGERPRINT_KEY)
            down_since = redis_client.get(DOWN_SINCE_KEY)

            if snapshot["healthy"]:
                if state == "degraded" and ALERT_RECOVERY_ENABLED:
                    recovery_msg = render_recovery(snapshot, down_since)
                    if send_telegram_html(recovery_msg):
                        LOGGER.warning("Recovery alert sent")
                redis_client.set(STATE_KEY, "healthy")
                redis_client.set(FINGERPRINT_KEY, snapshot["fingerprint"])
                redis_client.delete(DOWN_SINCE_KEY)
                time.sleep(ALERT_MONITOR_INTERVAL_SECONDS)
                continue

            if not down_since:
                down_since = utc_iso(snapshot["now"])
                redis_client.set(DOWN_SINCE_KEY, down_since)

            send_update = False
            update_mode = state == "degraded"

            if state != "degraded":
                send_update = True
            elif snapshot["fingerprint"] != (last_fingerprint or ""):
                send_update = True
            elif should_repeat(redis_client, now_ts):
                send_update = True

            if send_update:
                incident_msg = render_incident(snapshot, down_since, update_mode=update_mode)
                if send_telegram_html(incident_msg):
                    set_last_sent(redis_client, now_ts)
                    LOGGER.warning("Incident alert sent")

            redis_client.set(STATE_KEY, "degraded")
            redis_client.set(FINGERPRINT_KEY, snapshot["fingerprint"])

            time.sleep(ALERT_MONITOR_INTERVAL_SECONDS)

        except Exception as exc:
            LOGGER.exception("Worker alert monitor loop error: %s", exc)
            time.sleep(max(ALERT_MONITOR_INTERVAL_SECONDS, 30))


def main() -> None:
    LOGGER.info("Starting worker alert monitor")
    LOGGER.info("Environment=%s Redis=%s", ALERT_ENV_NAME, MONITOR_REDIS_URL)
    monitor_loop()


if __name__ == "__main__":
    main()
