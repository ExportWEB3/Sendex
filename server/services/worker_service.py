"""
Day 9: Background Worker Service

Processes email queues and campaigns:
- Pulls jobs from Redis queue
- Sends emails via SMTP
- Updates campaign progress
- Handles retries and failures
- Background scheduler for auto-tasks (warmup, auto-replies)
"""

import re as _re
import time
import signal
import threading
import logging
import os
import random
import json
from html import escape as _html_escape
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Callable
from concurrent.futures import ThreadPoolExecutor
import traceback
from services.redis_client import get_redis_client

from database import SessionLocal
from services.queue_service import EmailQueue, Priority, email_queue
from services import domain_service as _ds
from services.smtp_service import SMTPService
from services.campaign_service import CampaignService
from services.template_engine import TemplateEngine
from services.rate_limiter import RateLimiter
from services.campaign_logger import log_campaign_activity as _log_activity
from services.kill_switch import is_kill_switch_enabled, KILL_SWITCH_MESSAGE
from services.attachment_service import resolve_attachment_path
from services.sending_mode_service import (
    get_worker_delay, should_take_break, SendingMode,
    get_campaign_batch_params, determine_mode,
)
from models.campaign import Campaign, CampaignStatus, CampaignRecipient, RecipientSendStatus
from models.inbox import Inbox
from models.recipient import Recipient
from models.smtp_account import SMTPAccount

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class WorkerService:
    """
    Background worker that processes email queues.
    
    Features:
    - Multi-threaded processing
    - Graceful shutdown
    - Rate limiting per inbox
    - Automatic retries
    - Campaign progress tracking
    """
    
    def __init__(
        self,
        num_workers: int = 3,  # Safe: recipient claiming is atomic (SKIP LOCKED)
        poll_interval: float = 1.0,
        batch_size: int = 10
    ):
        self.num_workers = num_workers
        self.poll_interval = poll_interval
        self.batch_size = batch_size

        # Parallel campaign batchers — how many campaigns can be batching at once.
        self.batcher_threads = max(1, int(os.getenv("BATCHER_THREADS", "8")))
        
        self.queue = email_queue
        self.rate_limiter = RateLimiter()
        self.template_engine = TemplateEngine()
        
        self._running = False
        self._executor: Optional[ThreadPoolExecutor] = None
        self._threads = []
        
        # Timezone-aware worker — reads WORKER_TIMEZONE env var
        self.worker_timezone = os.getenv("WORKER_TIMEZONE")  # e.g. 'US/Eastern' or 'US/Central'

        # Instance name — unique ID for multiple workers on the same timezone.
        # Defaults to the timezone slug (e.g. 'us_eastern'). Set WORKER_NAME to
        # 'us_eastern_2' etc. for additional instances.
        self.worker_name = (os.getenv("WORKER_NAME") or "").strip() or self.worker_timezone
        
        # Redis for shared state (heartbeat)
        _redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self._redis = get_redis_client(_redis_url)
        self._heartbeat_key = "worker:heartbeat"
        self._heartbeat_thread = None
        
        # Human-like sending config — MODE-AWARE
        self._send_count = 0           # count since last mini break
        self._macro_send_count = 0     # count since last macro break
        self._current_worker_mode = None  # Resolved per-email from inbox sending_mode
        
        # Stats
        self.stats = {
            "started_at": None,
            "processed": 0,
            "sent": 0,
            "failed": 0,
            "retried": 0
        }
    
    # ── Timezone / Business Hours ──────────────────────────────────────
    
    def _is_business_hours(self) -> bool:
        """Check if it's currently within business hours (9AM-5PM Mon-Fri) in this worker's timezone."""
        if not self.worker_timezone:
            return True  # No timezone set → always allow (backward compat)
        try:
            from zoneinfo import ZoneInfo
            now_local = datetime.now(ZoneInfo(self.worker_timezone))
            # Monday=0 … Friday=4
            if now_local.weekday() > 4:
                return False
            return 9 <= now_local.hour < 17
        except Exception as e:
            logger.error(f"Timezone check error: {e}")
            return True  # On error, allow sending
    
    def _get_local_time_info(self) -> dict:
        """Get current local time info for this worker's timezone."""
        if not self.worker_timezone:
            return {"timezone": None, "local_time": None, "business_hours": True}
        try:
            from zoneinfo import ZoneInfo
            now_local = datetime.now(ZoneInfo(self.worker_timezone))
            in_biz = now_local.weekday() <= 4 and 9 <= now_local.hour < 17
            
            # Calculate next window open
            next_open = None
            if not in_biz:
                # Move to next 9 AM weekday
                candidate = now_local.replace(hour=9, minute=0, second=0, microsecond=0)
                if now_local.hour >= 17 or (now_local.hour >= 9 and now_local.weekday() <= 4):
                    # Past 5PM or it's a weekday after hours → go to next day
                    candidate += timedelta(days=1)
                elif now_local.hour < 9 and now_local.weekday() <= 4:
                    pass  # Same day, 9AM hasn't come yet
                # Skip weekends
                while candidate.weekday() > 4:
                    candidate += timedelta(days=1)
                next_open = candidate.isoformat()
            
            return {
                "timezone": self.worker_timezone,
                "local_time": now_local.strftime("%Y-%m-%d %H:%M:%S %Z"),
                "business_hours": in_biz,
                "next_window_open": next_open,
            }
        except Exception as e:
            logger.error(f"_get_local_time_info error: {e}")
            return {"timezone": self.worker_timezone, "local_time": None, "business_hours": True}
    
    def _seconds_until_next_window(self) -> int:
        """Calculate seconds until the next business-hours window opens.
        
        Used to re-queue emails that were popped outside the allowed
        sending window (e.g. a delay pushed the send past 5 PM).
        Returns 0 if already within business hours.
        """
        if not self.worker_timezone:
            return 0  # No timezone → no gate
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(self.worker_timezone)
            now_local = datetime.now(tz)

            # Already in window?
            if now_local.weekday() <= 4 and 9 <= now_local.hour < 17:
                return 0

            # Next 9 AM on a weekday
            candidate = now_local.replace(hour=9, minute=0, second=0, microsecond=0)
            if now_local.hour >= 9:  # past 9 AM today → move to tomorrow
                candidate += timedelta(days=1)
            # Skip weekends
            while candidate.weekday() > 4:
                candidate += timedelta(days=1)

            delta = (candidate - now_local).total_seconds()
            return max(int(delta), 60)  # At least 60 s to avoid tight loops
        except Exception as e:
            logger.error(f"_seconds_until_next_window error: {e}")
            return 300  # Fallback: 5 minutes

    def _tz_date_for(self, tz_name: Optional[str] = None) -> str:
        """Local date in ``tz_name`` (fallback UTC) as YYYY-MM-DD.

        Used to key per-inbox quota counters so they roll on the campaign
        owner's local day boundary.
        """
        try:
            from zoneinfo import ZoneInfo
            if tz_name:
                return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
        except Exception:
            pass
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _seconds_until_next_utc_day(self) -> int:
        """Seconds until the next UTC midnight (domain budget rollover)."""
        now = datetime.now(timezone.utc)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(int((tomorrow - now).total_seconds()), 60)

    def _seconds_until_next_day_window(self, tz_name: Optional[str] = None) -> int:
        """Seconds until the next business-hours window opens on the NEXT
        local day in ``tz_name`` (inbox quota rollover target)."""
        try:
            from zoneinfo import ZoneInfo
            if tz_name:
                now_local = datetime.now(ZoneInfo(tz_name))
            else:
                now_local = datetime.now(timezone.utc)
            candidate = now_local.replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
            while candidate.weekday() > 4:
                candidate += timedelta(days=1)
            delta = (candidate - now_local).total_seconds()
            return max(int(delta), 60)
        except Exception:
            return 86400

    def _campaign_inbox_cap(self, inbox) -> int:
        """DEPRECATED: campaigns are no longer gated by per-inbox caps.

        Kept for backward compatibility with any callers; returns unlimited.
        """
        return 0

    def _update_worker_redis_status(self, status: str, extra: dict = None):
        """Write this worker's status to Redis for API visibility."""
        if not self.worker_timezone:
            return
        key = f"worker:{self.worker_name.replace('/', '_').lower()}:status"
        info = self._get_local_time_info()
        info["worker_name"] = self.worker_name
        info["status"] = status  # 'sending', 'cooldown', 'idle'
        info["updated_at"] = datetime.now(timezone.utc).isoformat()
        if extra:
            info.update(extra)
        try:
            self._redis.set(key, json.dumps(info), ex=300)  # 5 min TTL
        except Exception:
            pass

    def _set_campaign_window_cooldown_status(self, info: Optional[dict] = None):
        """Expose campaign-level cooldown state while this timezone worker is outside hours."""
        if not self.worker_timezone:
            return

        info = info or self._get_local_time_info()
        remaining = max(0, self._seconds_until_next_window())
        next_window_open = info.get("next_window_open")
        local_time = info.get("local_time")
        ttl_seconds = max(120, min(86400, remaining + 180))

        db = SessionLocal()
        try:
            running_campaign_ids = [
                row[0]
                for row in db.query(Campaign.id).filter(
                    Campaign.status == CampaignStatus.RUNNING,
                    Campaign.send_timezone == self.worker_timezone,
                ).all()
            ]
        except Exception as e:
            logger.debug(f"Failed loading running campaigns for cooldown status: {e}")
            running_campaign_ids = []
        finally:
            db.close()

        if not running_campaign_ids:
            return

        status_payload = {
            "state": "cooldown",
            "reason": "outside_business_hours",
            "remaining": remaining,
            "next_window_open": next_window_open,
            "worker_timezone": self.worker_timezone,
            "message": f"Worker {self.worker_timezone} is outside business hours; waiting for next window.",
        }

        for campaign_id in running_campaign_ids:
            try:
                self.queue.redis.set(
                    f"campaign:{campaign_id}:status",
                    json.dumps(status_payload),
                    ex=ttl_seconds,
                )

                # Emit a sparse activity line so users understand why queueing is paused.
                warn_key = f"campaign:{campaign_id}:warn:outside_business_hours"
                if self.queue.redis.set(warn_key, "1", nx=True, ex=300):
                    if next_window_open:
                        _log_activity(
                            campaign_id,
                            f"⏰ {self.worker_timezone} worker is outside business hours ({local_time}). Next window: {next_window_open}",
                        )
                    else:
                        _log_activity(
                            campaign_id,
                            f"⏰ {self.worker_timezone} worker is outside business hours ({local_time}).",
                        )
            except Exception:
                continue

    def _set_campaign_kill_switch_status(self):
        """Expose kill-switch state on running campaigns and post a sparse activity line."""
        db = SessionLocal()
        try:
            query = db.query(Campaign.id).filter(Campaign.status == CampaignStatus.RUNNING)
            if self.worker_timezone:
                query = query.filter(Campaign.send_timezone == self.worker_timezone)
            running_campaign_ids = [row[0] for row in query.all()]
        except Exception as e:
            logger.debug(f"Failed loading running campaigns for kill switch status: {e}")
            running_campaign_ids = []
        finally:
            db.close()

        if not running_campaign_ids:
            return

        status_payload = {
            "state": "kill_switch",
            "reason": "kill_switch",
            "message": KILL_SWITCH_MESSAGE,
        }

        for campaign_id in running_campaign_ids:
            try:
                self.queue.redis.set(
                    f"campaign:{campaign_id}:status",
                    json.dumps(status_payload),
                    ex=300,
                )

                # Emit a sparse activity line so users understand why nothing is sending.
                warn_key = f"campaign:{campaign_id}:warn:kill_switch"
                if self.queue.redis.set(warn_key, "1", nx=True, ex=300):
                    _log_activity(campaign_id, f"🔒 {KILL_SWITCH_MESSAGE} — sending paused")
            except Exception:
                continue

    def start(self):
        """Start the worker service."""
        if is_kill_switch_enabled():
            logger.warning(f"KILL SWITCH ACTIVE: {KILL_SWITCH_MESSAGE} — worker will idle until disabled")
            print(f"\n*** {KILL_SWITCH_MESSAGE} — worker running in idle mode ***\n")
        
        if self._running:
            logger.warning("Worker already running")
            return
        
        self._running = True
        self.stats["started_at"] = datetime.now(timezone.utc).isoformat()
        
        tz_label = self.worker_timezone or 'any'
        logger.info(f"Worker starting — timezone={tz_label}, biz_hours=9AM-5PM Mon-Fri")
        self._update_worker_redis_status('starting')
        
        # Start heartbeat thread for cross-process status
        self._start_heartbeat()
        
        logger.info(f"Starting worker service with {self.num_workers} workers")
        
        # Start worker threads (+1 for scheduler, +N for the batcher pool)
        self._executor = ThreadPoolExecutor(
            max_workers=self.num_workers + self.batcher_threads + 1
        )
        
        for i in range(self.num_workers):
            future = self._executor.submit(self._worker_loop, i)
            self._threads.append(future)
        
        # Start campaign scheduler thread (refreshes campaign:due from DB)
        scheduler_future = self._executor.submit(self._campaign_scheduler_loop)
        self._threads.append(scheduler_future)
        
        # Start batcher pool — campaigns are batched in parallel
        for i in range(self.batcher_threads):
            future = self._executor.submit(self._batcher_loop, i)
            self._threads.append(future)
        
        logger.info(
            f"Worker service started (senders={self.num_workers}, batchers={self.batcher_threads})"
        )
    
    def stop(self, wait: bool = True):
        """Stop the worker service gracefully."""
        if not self._running:
            return
        
        logger.info("Stopping worker service...")
        self._running = False
        
        # Clear heartbeat in Redis
        try:
            self._redis.delete(self._heartbeat_key)
        except Exception:
            pass
        
        if self._executor:
            self._executor.shutdown(wait=wait)
        
        logger.info("Worker service stopped")
    
    def _start_heartbeat(self):
        """Start a background thread that writes a liveness heartbeat to Redis.

        Writes every 5s with a 30s TTL. The generous TTL-to-interval margin (6x)
        keeps the shared ``worker:heartbeat`` key alive through transient stalls
        (GIL contention during heavy sending, brief Redis slowness) that
        previously expired the old 10s TTL and triggered false "missing" alerts.
        """
        def _heartbeat_loop():
            while self._running:
                try:
                    self._redis.setex(self._heartbeat_key, 30, datetime.now(timezone.utc).isoformat())
                except Exception:
                    pass
                time.sleep(5)
        self._heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()
    
    @staticmethod
    def is_worker_alive() -> bool:
        """Check if the worker process is alive by reading Redis heartbeat."""
        try:
            _redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
            r = get_redis_client(_redis_url)
            return r.exists("worker:heartbeat") > 0
        except Exception:
            return False

    @staticmethod
    def _normalize_timezone_name(timezone_name: Optional[str]) -> Optional[str]:
        """Normalize timezone labels used for queue ownership checks."""
        if timezone_name is None:
            return None
        normalized = str(timezone_name).strip()
        return normalized or None

    def _resolve_campaign_timezone(self, campaign_id: Optional[int]) -> Optional[str]:
        """Load campaign timezone for legacy queued items that lack ownership metadata."""
        if not campaign_id:
            return None

        db = SessionLocal()
        try:
            campaign_tz = db.query(Campaign.send_timezone).filter(Campaign.id == campaign_id).scalar()
            return self._normalize_timezone_name(campaign_tz)
        except Exception as e:
            logger.warning(f"Failed to load send_timezone for campaign {campaign_id}: {e}")
            return None
        finally:
            db.close()

    def _enforce_campaign_timezone_ownership(self, email_data: Dict[str, Any], worker_id: int) -> bool:
        """Ensure only the owning timezone worker processes campaign emails."""
        worker_tz = self._normalize_timezone_name(self.worker_timezone)
        campaign_id = email_data.get("campaign_id")

        # Unscoped workers or non-campaign jobs can process normally.
        if not worker_tz or not campaign_id:
            return True

        owner_tz = self._normalize_timezone_name(email_data.get("_owner_timezone"))
        if not owner_tz:
            # Backfill ownership for legacy queued entries that predate this fix.
            owner_tz = self._resolve_campaign_timezone(campaign_id)
            if owner_tz:
                email_data["_owner_timezone"] = owner_tz

        if not owner_tz or owner_tz == worker_tz:
            return True

        email_id = email_data.get("_id", "unknown")
        to_email = email_data.get("to_email", "?")

        # Re-queue without mutating sent/failed metrics.
        self.queue.redis.hdel(self.queue.PROCESSING_KEY, email_id)
        self.queue.redis.srem(self.queue.ACTIVE_IDS_KEY, email_id)

        priority_raw = email_data.get("_priority", Priority.NORMAL.value)
        try:
            priority = Priority(priority_raw)
        except Exception:
            priority = Priority.NORMAL

        user_id = email_data.get("_user_id")
        self.queue.add(email_data, priority, delay_seconds=2, user_id=user_id)

        logger.warning(
            f"Worker {worker_id} [{worker_tz}] re-queued {email_id} ({to_email}) "
            f"for owner timezone {owner_tz}"
        )
        return False
    
    def _requeue_blocked_email(
        self,
        email_data: Dict[str, Any],
        worker_id: int,
        delay_seconds: int,
        reason: str = "blocked",
    ) -> None:
        """Return a popped email to the queue without counting it as sent/failed.

        Used for quota/health holds: the recipient stays PENDING and will be
        retried after ``delay_seconds`` (e.g. the next local day window).
        """
        email_id = email_data.get("_id", "unknown")
        to_email = email_data.get("to_email", "?")

        self.queue.redis.hdel(self.queue.PROCESSING_KEY, email_id)
        self.queue.redis.srem(self.queue.ACTIVE_IDS_KEY, email_id)

        priority_raw = email_data.get("_priority", Priority.NORMAL.value)
        try:
            priority = Priority(priority_raw)
        except Exception:
            priority = Priority.NORMAL

        user_id = email_data.get("_user_id")
        self.queue.add(email_data, priority, delay_seconds=delay_seconds, user_id=user_id)
        logger.info(
            f"Worker {worker_id} held {email_id} ({to_email}) for {delay_seconds}s — {reason}"
        )

    def _human_delay(self, inbox_mode: Optional[SendingMode] = None):
        """Add a MODE-AWARE human-like random delay between email sends.
        
        The delay is determined by the inbox's sending_mode:
        - Active:     short delays, breaks every 8-18 emails
        - Distracted: medium delays, breaks every 5-12 emails
        - Offline:    long delays, breaks every 2-5 emails
        
        Falls back to Offline mode if inbox lookup fails (safe default).
        
        Fix #5: Accepts the already-loaded mode directly instead of opening
        a new DB session per email to re-query the inbox.
        """
        mode = inbox_mode or SendingMode.ACTIVE
        
        self._send_count += 1
        self._macro_send_count += 1
        self._current_worker_mode = mode
        
        # The campaign batcher already handles all pacing (inter-email delays,
        # cooldowns, mini/macro breaks) via queue delay_seconds. The worker
        # only adds a small human variance on top — never long breaks.
        delay = get_worker_delay(mode)
        capped_delay = min(delay, 5.0)
        time.sleep(capped_delay)
    
    IDLE_SAFETY_INTERVAL = 60  # Safety fallback check when no wake-up hint (seconds)
    WAKE_KEY = "queue:wake_at"
    
    def _worker_loop(self, worker_id: int):
        """Main worker loop - processes queue items with wake-up hints.
        
        Instead of constant polling, the worker sleeps precisely until the next
        email is due. The campaign batcher sets a Redis 'queue:wake_at' key
        with the earliest send_after timestamp. If no hint exists, the worker
        does a safety check every 60s.
        """
        logger.info(f"Worker {worker_id} started")
        
        # Fix #8: Stagger counter — each re-queued email gets an increasing
        # offset so they don't all fire at the exact same time in the morning.
        _requeue_stagger_count = 0
        _REQUEUE_STAGGER_SEC = 90  # 90 seconds between staggered re-queues
        
        while self._running:
            try:
                # Kill switch check — pause processing while enabled
                if is_kill_switch_enabled():
                    logger.warning(f"Worker {worker_id}: {KILL_SWITCH_MESSAGE} — pausing")
                    time.sleep(10)
                    continue
                
                # Pop next item from queue (scoped to this worker's timezone ownership)
                email_data = self.queue.pop(worker_timezone=self.worker_timezone)
                
                if email_data:
                    if not self._enforce_campaign_timezone_ownership(email_data, worker_id):
                        continue

                    # ── POST-DELAY BUSINESS HOURS CHECK ──────────────────
                    # The email may have been queued hours ago with a delay.
                    # By the time it fires, it could be outside the allowed
                    # sending window. Re-queue it for the next valid window
                    # with staggered spacing to prevent clustering (Fix #8).
                    email_type = (email_data.get("email_type") or "").lower()
                    is_campaign_email = bool(email_data.get("campaign_id")) or email_type in {
                        "campaign",
                        "campaign_email",
                    }

                    # Only campaign sends are hard-gated by business hours.
                    # Warmup/replies/transactional emails should continue.
                    if is_campaign_email and not self._is_business_hours():
                        base_delay = self._seconds_until_next_window()
                        stagger = _requeue_stagger_count * _REQUEUE_STAGGER_SEC
                        delay = base_delay + stagger
                        _requeue_stagger_count += 1
                        
                        email_id = email_data.get("_id", "unknown")
                        to_email = email_data.get("to_email", "?")
                        campaign_id = email_data.get("campaign_id")
                        tz_label = self.worker_timezone or 'any'
                        logger.info(
                            f"Worker {worker_id} [{tz_label}]: Outside business hours — "
                            f"re-queuing {to_email} ({email_id}) with {delay}s delay "
                            f"(base={base_delay}s + stagger={stagger}s)"
                        )
                        if campaign_id:
                            _log_activity(
                                campaign_id,
                                f"⏰ Outside business hours — re-queued {to_email} "
                                f"for next window (~{delay // 60}min, slot #{_requeue_stagger_count})"
                            )
                        # Remove from processing set and active IDs WITHOUT
                        # counting as sent/failed (complete() would inflate stats).
                        self.queue.redis.hdel(self.queue.PROCESSING_KEY, email_id)
                        self.queue.redis.srem(self.queue.ACTIVE_IDS_KEY, email_id)
                        # Re-add with staggered delay to next window
                        user_id = email_data.get("_user_id")
                        self.queue.add(email_data, Priority.NORMAL, delay_seconds=delay, user_id=user_id)
                        
                        # Fix #9: After re-queuing, sleep until next window instead
                        # of churning through every remaining email in the queue.
                        logger.info(
                            f"Worker {worker_id} [{tz_label}]: Sleeping {base_delay}s "
                            f"until next business-hours window"
                        )
                        # Sleep in small chunks so we stay responsive to stop signals
                        sleep_remaining = base_delay
                        while sleep_remaining > 0 and self._running:
                            chunk = min(sleep_remaining, 30.0)
                            time.sleep(chunk)
                            sleep_remaining -= chunk
                        _requeue_stagger_count = 0  # Reset for next cycle
                        continue

                    # Reset stagger if we're in business hours (normal sending)
                    _requeue_stagger_count = 0
                    
                    self._process_email(email_data, worker_id)
                    # Campaign pacing lives entirely in queue scores + domain
                    # buckets, so campaign sends skip the per-send sleep.
                    # Warmup/replies keep the human-like mode-aware delay.
                    is_campaign_send = is_campaign_email
                    if not is_campaign_send:
                        self._human_delay(inbox_mode=self._current_worker_mode)
                else:
                    # Nothing ready right now — use wake-up hint to sleep precisely
                    self._smart_sleep(worker_id)
                    
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}")
                traceback.print_exc()
                time.sleep(1)
        
        logger.info(f"Worker {worker_id} stopped")
    
    def _smart_sleep(self, worker_id: int):
        """Sleep until the next email is due, or do a safety check after IDLE_SAFETY_INTERVAL.
        
        Uses the Redis 'queue:wake_at' key set by the campaign batcher.
        If a wake-up hint exists and the time is in the future, sleep until then.
        If no hint exists, peek at the queue for the next scheduled email.
        If the queue is completely empty, sleep for IDLE_SAFETY_INTERVAL as a fallback.
        """
        try:
            wake_val = self.queue.redis.get(self.WAKE_KEY)
            
            if wake_val:
                wake_at = float(wake_val)
                wait_seconds = wake_at - time.time()
                
                if wait_seconds <= 0:
                    # Wake time already passed — clear it and re-check queue immediately
                    self.queue.redis.delete(self.WAKE_KEY)
                    time.sleep(0.5)
                    return
                
                # Sleep in small increments so we can still respond to stop signals
                # and not overshoot if a new earlier wake_at gets set
                sleep_time = min(wait_seconds, 10.0)
                time.sleep(sleep_time)
            else:
                # No batcher hint — peek at the queue for the next scheduled email
                next_scheduled = self.queue.peek_next_scheduled()
                
                if next_scheduled is not None:
                    wait_seconds = next_scheduled - time.time()
                    if wait_seconds <= 0:
                        # Ready now, re-check immediately
                        time.sleep(0.5)
                        return
                    
                    # Set wake_at so other workers can also use this hint
                    self.queue.redis.set(self.WAKE_KEY, str(next_scheduled), ex=max(int(wait_seconds) + 60, 120))
                    sleep_time = min(wait_seconds, 10.0)
                    time.sleep(sleep_time)
                else:
                    # Queue is completely empty — idle safety check
                    # Sleep in small chunks to remain responsive to stop
                    for _ in range(self.IDLE_SAFETY_INTERVAL):
                        if not self._running:
                            return
                        # Break early if a wake_at appears (new batch was queued)
                        if self.queue.redis.exists(self.WAKE_KEY):
                            return
                        time.sleep(1)
        except Exception:
            time.sleep(2)
    
    def _process_email(self, email_data: Dict[str, Any], worker_id: int):
        """Process a single email from the queue."""
        email_id = email_data.get("_id", "unknown")
        
        # Fix #1: Open DB session ONCE at the top so it's always closed,
        # even if the outer except block needs it for error handling.
        db = SessionLocal()
        try:
            self.stats["processed"] += 1
            
            # Extract email details
            inbox_id = email_data.get("inbox_id")
            to_email = email_data.get("to_email")
            subject = email_data.get("subject")
            body_html = email_data.get("body_html")
            body_text = email_data.get("body_text")
            reply_to = email_data.get("reply_to")
            
            # Campaign tracking
            campaign_id = email_data.get("campaign_id")
            recipient_id = email_data.get("recipient_id")
            
            if not all([inbox_id, to_email, subject]):
                raise ValueError("Missing required email fields")
            # DEDUPLICATION CHECK: If this is a campaign email, check if already sent
            if campaign_id and recipient_id:
                existing = db.query(CampaignRecipient).filter(
                    CampaignRecipient.campaign_id == campaign_id,
                    CampaignRecipient.recipient_id == recipient_id,
                    CampaignRecipient.status == RecipientSendStatus.SENT
                ).first()
                
                if existing:
                    logger.warning(f"Worker {worker_id}: Email to {to_email} already sent, skipping duplicate")
                    if campaign_id:
                        _log_activity(campaign_id, f"⚠️  Skipped duplicate → {to_email}")
                    self.queue.complete(email_id, success=True)
                    return
            
            # ── PAUSE/CANCEL CHECK: Don't send if campaign was paused or cancelled ──
            # Emails already in the Redis queue with countdown delays will still
            # pop after pause — this check is the only thing that stops them.
            if campaign_id:
                _campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
                if _campaign and _campaign.status in (CampaignStatus.PAUSED, CampaignStatus.CANCELLED):
                    # Revert recipient back to pending so it gets picked up on resume
                    cr = db.query(CampaignRecipient).filter(
                        CampaignRecipient.campaign_id == campaign_id,
                        CampaignRecipient.recipient_id == recipient_id
                    ).first()
                    if cr and cr.status == RecipientSendStatus.PENDING:
                        cr.queued_at = None  # Un-queue so it's re-drawn on resume
                        db.commit()
                    self.queue.complete(email_id, success=True)  # Remove cleanly from queue
                    _log_activity(campaign_id, f"⏸️  Skipped ({_campaign.status.value}) → {to_email}")
                    logger.info(f"Worker {worker_id}: Skipped {to_email} — campaign {campaign_id} is {_campaign.status.value}")
                    return
            
            inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
            if not inbox:
                raise ValueError(f"Inbox {inbox_id} not found")
            
            # Fix #5: Store inbox mode so the worker loop can pass it
            # to _human_delay without a separate DB query.
            self._current_worker_mode = inbox.sending_mode or SendingMode.ACTIVE
            
            # Get SMTP account for this inbox
            smtp_account = db.query(SMTPAccount).filter(
                SMTPAccount.id == inbox.smtp_account_id
            ).first()
            if not smtp_account:
                # Check if this is a fallback email auto-reply
                if email_data.get("use_fallback"):
                    smtp_account = None  # Will use fallback path below
                else:
                    raise ValueError(f"SMTP account not found for inbox {inbox_id}")
            
            # Check if we should use fallback SMTP
            use_fallback = email_data.get("use_fallback", False)
            if use_fallback and email_data.get("fallback_smtp_host"):
                # Create a temporary SMTP account-like object for the fallback
                class FallbackSMTP:
                    def __init__(self, data):
                        self.host = data["fallback_smtp_host"]
                        self.port = data.get("fallback_smtp_port", 587)
                        self.username = data["fallback_email"]
                        self.password = data["fallback_password"]
                        self.from_email = data["fallback_email"]
                        self.from_name = ""
                        self.use_tls = True
                        self.use_ssl = False
                        self.is_active = True
                
                smtp_account = FallbackSMTP(email_data)
                logger.info(f"Worker {worker_id}: Using fallback SMTP {email_data['fallback_email']} for {to_email}")
            
            # ── Rate limiting ────────────────────────────────────────────────
            # Consolidated model: campaigns are gated ONLY by the fleet-wide
            # domain budget (reserved at queue time, 100k/day UTC) + the
            # per-minute send slots + the circuit breaker below. Per-inbox
            # warmup caps are enforced by the warmup service at queue time and
            # do not gate campaign sending. There is no send-time inbox check.
            send_domain = _ds.extract_domain(getattr(smtp_account, "from_email", None))
            if send_domain and _ds.is_throttled(send_domain):
                throttle_reason = _ds.get_throttle_reason(send_domain) or "domain health protection active"
                if campaign_id:
                    _log_activity(
                        campaign_id,
                        f"🔒 Domain safety pause ({throttle_reason}) — {to_email} held",
                    )
                self._requeue_blocked_email(
                    email_data, worker_id, self._seconds_until_next_utc_day(), "domain_throttled"
                )
                return

            if send_domain and not _ds.acquire_send_slot(send_domain):
                self._requeue_blocked_email(email_data, worker_id, 120, "domain_rate_limit")
                return
            
            # Create sending service based on provider type
            # (Fix #6: SMTPService imported at top of file)
            smtp = None
            api_sender = None  # Resend service instance

            provider = getattr(smtp_account, 'provider_type', 'smtp') or 'smtp'
            if provider in ('brevo', 'ses_api'):
                from services.resend_service import get_resend_service
                api_sender = get_resend_service()
                if not api_sender:
                    raise ValueError("Resend API not configured. Set RESEND_API_KEY in .env")
            else:
                # Default: SMTP service
                smtp = SMTPService(smtp_account=smtp_account)
            
            # Resolve reply-to
            # For Resend: only set Reply-To if the
            # campaign or inbox EXPLICITLY configured one.  Omitting it lets
            # the email client reply to the From address, which the catch-all
            # IMAP picks up — no scattered routing, no visible Reply-To header.
            # For SMTP senders: fall back to smtp_account.from_email as before.
            effective_reply_to = reply_to
            if not effective_reply_to and inbox.reply_to_email:
                effective_reply_to = inbox.reply_to_email
            if not effective_reply_to and api_sender is None:
                # SMTP path only — needs an explicit reply-to
                effective_reply_to = smtp_account.from_email if smtp_account else inbox.email

            # Optional per-recipient sender display-name override from queue payload.
            from_name_override = email_data.get("from_name_override")
            
            # Load file attachments if present
            attachment_list = None
            if email_data.get("attachments"):
                attachment_list = []
                for att in email_data["attachments"]:
                    filepath = resolve_attachment_path(att.get("stored_name", ""))
                    if filepath:
                        with open(filepath, "rb") as af:
                            attachment_list.append({
                                "filename": att.get("filename", os.path.basename(filepath)),
                                "content": af.read(),
                            })
            
            # Send email via chosen service
            # Thread ID for conversation tracking (if available)
            thread_id_raw = email_data.get("thread_id")
            full_thread_id_for_header = None
            if thread_id_raw:
                from services.thread_id import make_full_thread_id, parse_thread_id as _parse_tid
                # If this is already a full thread ID (MMMMMMM-CCCCCCCC-NNN), use directly
                # Otherwise it's a base thread ID (MMMMMMM-CCCCCCCC), add -000
                if _parse_tid(thread_id_raw):
                    full_thread_id_for_header = thread_id_raw
                else:
                    full_thread_id_for_header = make_full_thread_id(thread_id_raw, 0)

            # Final send-boundary guard. This closes the race where the switch
            # is enabled after a worker pops an item but before the provider call.
            if is_kill_switch_enabled():
                if campaign_id:
                    _log_activity(
                        campaign_id,
                        f"🔒 {KILL_SWITCH_MESSAGE} — {to_email} held",
                    )
                self._requeue_blocked_email(email_data, worker_id, 300, "global_kill_switch")
                return
            
            if api_sender is not None:
                # Extract unsubscribe URL from rendered HTML for List-Unsubscribe header
                # (Fix #6: re imported at top of file as _re)
                send_headers = {}
                unsub_match = _re.search(r'href=["\']([^"\']*?/unsubscribe/[^"\']+)["\']', body_html or '')
                if unsub_match:
                    unsub_url = unsub_match.group(1)
                    send_headers['List-Unsubscribe'] = f'<{unsub_url}>'
                    send_headers['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
                
                # Embed thread ID in custom header for reply tracking
                if full_thread_id_for_header:
                    send_headers['X-Thread-ID'] = full_thread_id_for_header
                
                # Resend API sending supports HTML, plain text, and attachments.
                resp = api_sender.send_email_sync(
                    to_email=to_email,
                    to_name=None,
                    from_email=smtp_account.from_email,
                    from_name=from_name_override or smtp_account.from_name or None,
                    subject=subject,
                    html_content=body_html or None,
                    text_content=body_text or None,
                    reply_to=effective_reply_to,
                    headers=send_headers if send_headers else None,
                    attachments=attachment_list,
                )
                if not resp.get('success'):
                    raise Exception(f"Resend send failed: {resp.get('error')}")
            else:
                # Build custom headers for SMTP (thread ID + custom Message-ID)
                smtp_custom_headers = {}
                custom_message_id = None
                if full_thread_id_for_header:
                    smtp_custom_headers['X-Thread-ID'] = full_thread_id_for_header
                    # Embed thread ID in Message-ID for reference-based reply matching
                    from_domain = smtp_account.from_email.split('@')[-1] if smtp_account.from_email else 'mail.local'
                    custom_message_id = f"<{full_thread_id_for_header}@{from_domain}>"
                
                # Use SMTPService (existing path)
                resp = smtp.send_email(
                    to_email=to_email,
                    subject=subject,
                    body_html=body_html,
                    body_text=body_text,
                    from_name=from_name_override or smtp_account.from_name or None,
                    reply_to=effective_reply_to,
                    attachments=attachment_list,
                    custom_headers=smtp_custom_headers if smtp_custom_headers else None,
                    custom_message_id=custom_message_id,
                )
            
            # Extract sent message_id for reply matching
            sent_message_id = None
            if api_sender is not None and resp:
                raw_id = resp.get('message_id', '')
                if raw_id:
                    # Resend returns 're_abc123'; the actual Message-ID header
                    # will be '<re_abc123@resend.dev>'. Store the angle-bracket form
                    # so it matches the IMAP In-Reply-To header directly.
                    sent_message_id = f'<{raw_id}@resend.dev>' if not raw_id.startswith('<') else raw_id
            else:
                # SMTP path: capture the Message-ID from the sent message
                if resp and resp.get('message_id'):
                    raw_smtp_id = resp['message_id']
                    sent_message_id = raw_smtp_id if raw_smtp_id.startswith('<') else f'<{raw_smtp_id}>'
            
            # Record domain health stat (for the circuit breaker)
            _ds.record_sent(send_domain)
            
            # Update campaign recipient if applicable
            if campaign_id and recipient_id:
                self._update_campaign_recipient(
                    db, campaign_id, recipient_id, 
                    success=True,
                    sent_message_id=sent_message_id,
                    sent_subject=subject,
                    sent_body_html=body_html,
                    sent_from_email=smtp_account.from_email if smtp_account else None,
                    sent_from_name=(from_name_override or (smtp_account.from_name if smtp_account else None)),
                    template_id=email_data.get("template_id")
                )
            
            # Update auto-reply status if this is an auto-reply
            email_type = email_data.get("email_type")
            if email_type == "campaign_auto_reply":
                from models.warmup import CampaignAutoReply
                # Find auto-reply by ID if available, otherwise by to_email
                auto_reply_id = email_data.get("auto_reply_id")
                if auto_reply_id:
                    auto_reply = db.query(CampaignAutoReply).filter(
                        CampaignAutoReply.id == auto_reply_id
                    ).first()
                else:
                    # Fallback for old queue items without auto_reply_id
                    auto_reply = db.query(CampaignAutoReply).filter(
                        CampaignAutoReply.to_email == to_email,
                        CampaignAutoReply.status == "queued"
                    ).first()
                if auto_reply:
                    auto_reply.status = "sent"
                    auto_reply.sent_at = datetime.now(timezone.utc)
                    db.commit()
                    logger.info(f"Auto-reply {auto_reply.id} to {to_email} marked as sent")
            
            # Update inbox warmup counters on successful warmup send
            email_type = email_data.get("email_type")
            if email_type == "warmup" and inbox:
                inbox.current_daily_count = (inbox.current_daily_count or 0) + 1
                inbox.total_sent = (inbox.total_sent or 0) + 1
                inbox.last_sent_at = datetime.now(timezone.utc)
                db.commit()
            
            # Mark complete
            self.queue.complete(email_id, success=True)
            self.stats["sent"] += 1
            
            # Log activity for campaign emails
            if campaign_id:
                inbox_tag = f" : {inbox.email}" if inbox else ""
                _log_activity(campaign_id, f"✅ Sent → {to_email}{inbox_tag}")
            
            logger.info(f"Worker {worker_id} sent email to {to_email}")
            
        except Exception as e:
            logger.error(f"Worker {worker_id} failed to send {email_id}: {e}")
            self.queue.complete(email_id, success=False, error=str(e))
            self.stats["failed"] += 1
            
            # Log activity for campaign failures
            campaign_id_fail = email_data.get("campaign_id") if email_data else None
            to_email_fail = email_data.get("to_email", "unknown") if email_data else "unknown"
            inbox_id_fail = email_data.get("inbox_id") if email_data else None
            if campaign_id_fail:
                inbox_tag_fail = ""
                if inbox_id_fail:
                    # Fix #1: Reuse the same db session instead of opening a new one
                    try:
                        _ib = db.query(Inbox).filter(Inbox.id == inbox_id_fail).first()
                        if _ib:
                            inbox_tag_fail = f" : {_ib.email}"
                    except Exception:
                        pass
                _log_activity(campaign_id_fail, f"❌ Failed → {to_email_fail}{inbox_tag_fail}: {str(e)[:80]}")
            
            # Update campaign recipient if applicable
            if email_data.get("campaign_id") and email_data.get("recipient_id"):
                try:
                    self._update_campaign_recipient(
                        db, 
                        email_data["campaign_id"], 
                        email_data["recipient_id"],
                        success=False,
                        error=str(e)
                    )
                except Exception:
                    pass  # Don't let error-handling errors mask the original
        finally:
            # Fix #1: Single close — guaranteed to run in all paths
            db.close()
    
    def _update_campaign_recipient(
        self, 
        db, 
        campaign_id: int, 
        recipient_id: int,
        success: bool,
        error: Optional[str] = None,
        sent_message_id: Optional[str] = None,
        sent_subject: Optional[str] = None,
        sent_body_html: Optional[str] = None,
        sent_from_email: Optional[str] = None,
        sent_from_name: Optional[str] = None,
        template_id: Optional[int] = None
    ):
        """Update campaign recipient status after send attempt."""
        try:
            campaign_recipient = db.query(CampaignRecipient).filter(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.recipient_id == recipient_id
            ).first()
            
            if campaign_recipient:
                if success:
                    campaign_recipient.status = RecipientSendStatus.SENT
                    campaign_recipient.sent_at = datetime.now(timezone.utc)
                    if sent_message_id:
                        campaign_recipient.sent_message_id = sent_message_id
                    
                    # Capture sent content for preview/audit trail
                    if sent_subject is not None:
                        campaign_recipient.sent_subject = sent_subject
                    if sent_body_html is not None:
                        campaign_recipient.sent_body_html = sent_body_html
                    if sent_from_email is not None:
                        campaign_recipient.sent_from_email = sent_from_email
                    if sent_from_name is not None:
                        campaign_recipient.sent_from_name = sent_from_name
                    if template_id is not None:
                        campaign_recipient.template_id = template_id
                    
                    # ── Fix: Increment inbox daily counters on campaign sends ──
                    # Without this, the mode engine's "80% cap → OFFLINE" rule
                    # is blind to campaign volume (only warmup used to count).
                    inbox_id = campaign_recipient.inbox_id
                    if inbox_id:
                        inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
                        if inbox:
                            inbox.current_daily_count = (inbox.current_daily_count or 0) + 1
                            inbox.total_sent = (inbox.total_sent or 0) + 1
                            inbox.last_sent_at = datetime.now(timezone.utc)
                else:
                    campaign_recipient.status = RecipientSendStatus.FAILED
                    campaign_recipient.error_message = error
                
                db.commit()
                
                # Update campaign progress
                self._update_campaign_progress(db, campaign_id)
                
        except Exception as e:
            logger.error(f"Failed to update campaign recipient: {e}")
            db.rollback()
    
    def _update_campaign_progress(self, db, campaign_id: int):
        """Update campaign progress stats."""
        try:
            campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
            if not campaign:
                return
            
            # Count statuses
            from sqlalchemy import func
            
            counts = db.query(
                CampaignRecipient.status,
                func.count(CampaignRecipient.id)
            ).filter(
                CampaignRecipient.campaign_id == campaign_id
            ).group_by(CampaignRecipient.status).all()
            
            status_counts = {str(status.value): count for status, count in counts}
            
            sent = status_counts.get("sent", 0)
            failed = status_counts.get("failed", 0)
            pending = status_counts.get("pending", 0)
            
            campaign.total_sent = sent
            campaign.total_failed = failed
            campaign.send_progress = {
                "total": campaign.total_recipients,
                "sent": sent,
                "failed": failed,
                "pending": pending
            }
            
            # Check if complete
            # Fix #2: Don't mark COMPLETED if there are still emails in the
            # Redis queue for this campaign (e.g. re-queued by business-hours
            # check). Only complete when pending DB recipients == 0 AND no
            # campaign emails remain in the queue.
            if pending == 0 and sent + failed > 0:
                # Check if any emails for this campaign are still in Redis
                queue_has_emails = False
                try:
                    prefix = f"campaign:{campaign_id}:recipient:"
                    for p in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
                        queue_key = self.queue.QUEUE_KEY.format(priority=p.value)
                        # Peek at first few entries — if any match, don't complete
                        items = self.queue.redis.zrange(queue_key, 0, 50)
                        for item_json in items:
                            try:
                                data = json.loads(item_json)
                                if data.get("_id", "").startswith(prefix):
                                    queue_has_emails = True
                                    break
                            except (json.JSONDecodeError, TypeError):
                                continue
                        if queue_has_emails:
                            break
                except Exception:
                    pass  # On error, allow completion (don't block forever)
                
                if not queue_has_emails:
                    campaign.status = CampaignStatus.COMPLETED
                    campaign.completed_at = datetime.now(timezone.utc)
            
            db.commit()
            
        except Exception as e:
            logger.error(f"Failed to update campaign progress: {e}")
            db.rollback()
    
    def _campaign_scheduler_loop(self):
        """Scheduler: keep ``campaign:due`` fresh from the DB and drain due
        campaigns into the work queue.

        This is a lightweight ID-only scan (every ~15s). Heavy per-campaign
        work happens in the batcher pool, so hundreds of campaigns progress
        in parallel instead of serially.
        """
        tz_label = self.worker_timezone or 'any'
        logger.info(f"Campaign scheduler thread started (timezone={tz_label}) - scanning every 15 seconds")
        
        while self._running:
            try:
                # Kill switch check — skip processing while enabled
                if is_kill_switch_enabled():
                    logger.warning(f"Campaign scheduler: {KILL_SWITCH_MESSAGE} — pausing")
                    self._update_worker_redis_status('kill_switch')
                    self._set_campaign_kill_switch_status()
                    time.sleep(10)
                    continue
                
                # ── Business hours gate ──
                if not self._is_business_hours():
                    info = self._get_local_time_info()
                    self._update_worker_redis_status('cooldown')
                    self._set_campaign_window_cooldown_status(info)
                    logger.info(f"Worker [{tz_label}] outside business hours ({info.get('local_time', '?')}). "
                                f"Next window: {info.get('next_window_open', '?')}. Sleeping 60s...")
                    time.sleep(60)
                    continue

                running_count = 0
                scheduled_started = 0
                
                db = SessionLocal()
                try:
                    # Find running campaign IDs — filtered by this worker's timezone
                    query = db.query(Campaign.id).filter(
                        Campaign.status == CampaignStatus.RUNNING
                    )
                    if self.worker_timezone:
                        query = query.filter(Campaign.send_timezone == self.worker_timezone)
                    running_ids = [row[0] for row in query.all()]
                    running_count = len(running_ids)
                    
                    if running_count:
                        logger.info(f"[{tz_label}] Found {running_count} running campaigns")
                    
                    # Check scheduled campaigns — also filtered by timezone
                    now = datetime.now(timezone.utc)
                    sched_query = db.query(Campaign).filter(
                        Campaign.status == CampaignStatus.SCHEDULED,
                        Campaign.scheduled_at <= now
                    )
                    if self.worker_timezone:
                        sched_query = sched_query.filter(Campaign.send_timezone == self.worker_timezone)
                    scheduled = sched_query.all()
                    
                    for campaign in scheduled:
                        campaign.status = CampaignStatus.RUNNING
                        campaign.started_at = now
                        db.commit()
                        scheduled_started += 1
                        running_ids.append(campaign.id)
                        logger.info(f"[{tz_label}] Started scheduled campaign {campaign.id}")
                        # Queue first micro-batch immediately (own timezone).
                        self.queue.schedule_campaign(campaign.id, tz=self.worker_timezone)
                
                finally:
                    db.close()

                # Refresh the due set with NX so existing future due scores
                # (pacing/cooldown schedules) are never disturbed. This also
                # self-heals after Redis restarts or worker redeploys.
                # Scoped to THIS worker's timezone so another timezone's
                # workers never batch these campaigns.
                if running_ids:
                    now_ts = time.time()
                    try:
                        pipe = self.queue.redis.pipeline()
                        due_key = self.queue.campaign_due_key(self.worker_timezone)
                        for cid in set(running_ids):
                            pipe.zadd(due_key, {str(cid): now_ts}, nx=True)
                        pipe.execute()
                    except Exception:
                        pass

                moved = self.queue.drain_due_campaigns(limit=500, tz=self.worker_timezone)

                active_campaigns = running_count + scheduled_started
                if moved > 0 or active_campaigns > 0:
                    state = 'sending'
                else:
                    state = 'idle'

                self._update_worker_redis_status(
                    state,
                    extra={
                        "active_campaigns": active_campaigns,
                        "moved_to_work": moved,
                        "batchers": self.batcher_threads,
                    },
                )
                
                # Sleep between cycles
                time.sleep(15)
                
            except Exception as e:
                logger.error(f"Campaign scheduler error: {e}")
                traceback.print_exc()
                time.sleep(15)
        
        logger.info("Campaign scheduler stopped")

    def _next_due_for_campaign(self, campaign_id: int, default_delay: int = 30) -> float:
        """Compute when ``campaign_id`` should next be batched.

        Prefers the campaign-level pacing key written by the batch engine,
        falls back to the earliest per-inbox cooldown, then a short default.
        """
        r = self.queue.redis
        try:
            pacing = r.get(f"campaign:{campaign_id}:next_batch")
            if pacing:
                t = float(pacing)
                if t > time.time():
                    return t
        except (TypeError, ValueError):
            pass

        earliest = None
        try:
            for key in r.scan_iter(match=f"campaign:{campaign_id}:inbox:*:next_batch"):
                try:
                    val = r.get(key)
                    if val:
                        t = float(val)
                        if t > time.time() and (earliest is None or t < earliest):
                            earliest = t
                except (TypeError, ValueError):
                    continue
        except Exception:
            pass

        if earliest is not None:
            return earliest

        return time.time() + default_delay

    def _batcher_loop(self, batcher_id: int):
        """Pull due campaign IDs from the work queue and batch one campaign each.

        Multiple batcher threads run in parallel so hundreds of campaigns make
        progress simultaneously. Per-campaign exclusivity is enforced by the
        ``campaign:{id}:processing`` Redis lock inside ``_process_campaign_batch``.
        """
        tz_label = self.worker_timezone or 'any'
        logger.info(f"Batcher {batcher_id} started (timezone={tz_label})")

        while self._running:
            try:
                if is_kill_switch_enabled():
                    time.sleep(10)
                    continue

                popped = self.queue.redis.blpop(
                    self.queue.campaign_work_key(self.worker_timezone),
                    timeout=5,
                )
                if popped is None:
                    continue
                _, raw_id = popped
                try:
                    campaign_id = int(raw_id)
                except (TypeError, ValueError):
                    continue

                # Re-arm guard: keep this campaign out of the work queue for a
                # short window so other batchers don't grab it while we process
                # (the per-campaign lock also protects correctness).
                self.queue.schedule_campaign(
                    campaign_id,
                    due_at=time.time() + 120,
                    tz=self.worker_timezone,
                )

                if not self._is_business_hours():
                    next_open = time.time() + self._seconds_until_next_window()
                    self.queue.schedule_campaign(
                        campaign_id,
                        due_at=next_open,
                        tz=self.worker_timezone,
                    )
                    continue

                db = SessionLocal()
                try:
                    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
                    if campaign and campaign.status == CampaignStatus.RUNNING:
                        self._process_campaign_batch(db, campaign)
                except Exception:
                    logger.error(f"Batcher {batcher_id} failed processing campaign {campaign_id}")
                    traceback.print_exc()
                finally:
                    db.close()

                self.queue.schedule_campaign(
                    campaign_id,
                    due_at=self._next_due_for_campaign(campaign_id),
                    tz=self.worker_timezone,
                )
            except Exception as e:
                logger.error(f"Batcher {batcher_id} error: {e}")
                time.sleep(1)

        logger.info(f"Batcher {batcher_id} stopped")
    
    def _atomic_claim_recipient_ids(self, db, ids: list) -> list:
        """Atomically claim PENDING, unqueued recipient rows via
        UPDATE ... RETURNING (Postgres).

        Returns the subset of ``ids`` actually claimed by this caller.
        Two concurrent callers can never claim the same row.
        """
        if not ids:
            return []
        from sqlalchemy import text as _sql_text
        rows = db.execute(
            _sql_text(
                "UPDATE campaign_recipients "
                "SET queued_at = NOW() "
                "WHERE id = ANY(:ids) "
                "  AND status = 'PENDING' "
                "  AND queued_at IS NULL "
                "RETURNING id"
            ),
            {"ids": ids},
        ).fetchall()
        return [row[0] for row in rows]

    def _get_rotation_template(self, db, campaign: Campaign):
        """
        Get the current template for multi-template rotation.
        
        Rotation pattern: 4-6-4-6-4-6... alternating batch sizes.
        Templates are shuffled randomly at campaign start.
        After sending batch_size emails with one template, advances to next.
        Wraps around to first template after exhausting all.
        
        Returns (subject, html, text, attachments) or None if not using rotation.
        """
        if not campaign.template_ids or not campaign.template_rotation_state:
            return None
        
        state = campaign.template_rotation_state
        shuffled = state.get("shuffled_order", [])
        if not shuffled:
            return None
        
        idx = state.get("current_index", 0)
        template_id = shuffled[idx % len(shuffled)]
        
        from models.ses_template import SESEmailTemplate
        template = db.query(SESEmailTemplate).filter(
            SESEmailTemplate.id == template_id
        ).first()
        
        if not template:
            logger.warning(f"Template {template_id} not found for rotation, falling back to campaign body")
            return None
        
        return {
            "template_id": template.id,
            "template_name": template.name,
            "subject": template.subject_line,
            "html": template.html_content,
            "text": template.text_fallback,
            "attachments": template.attachments,
        }
    
    def _advance_rotation(self, db, campaign: Campaign):
        """
        Advance the rotation state after queuing one email.
        
        Increments current_batch_sent. When batch exhausted:
        - Move to next template (current_index++)
        - Toggle batch size: 4 -> 6 -> 4 -> 6...
        - Wrap current_index when all templates exhausted
        """
        if not campaign.template_rotation_state:
            return
        
        state = dict(campaign.template_rotation_state)  # Copy to trigger SQLAlchemy dirty
        shuffled = state.get("shuffled_order", [])
        if not shuffled:
            return
        
        state["current_batch_sent"] = state.get("current_batch_sent", 0) + 1
        batch_size = state.get("current_batch_size", 4)
        
        if state["current_batch_sent"] >= batch_size:
            # Batch exhausted — advance to next template
            state["current_index"] = (state.get("current_index", 0) + 1) % len(shuffled)
            state["current_batch_sent"] = 0
            # Toggle: False(4) -> True(6) -> False(4) -> ...
            toggle = state.get("batch_toggle", False)
            state["batch_toggle"] = not toggle
            state["current_batch_size"] = 6 if not toggle else 4
        
        campaign.template_rotation_state = state
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(campaign, 'template_rotation_state')
        # NOTE: commit is done by the caller after each recipient
    
    def _process_campaign_batch(self, db, campaign: Campaign) -> int:
        """Queue a micro-batch of emails with human-like pacing.
        
        Pacing is MODE-AWARE — each inbox's sending_mode determines:
        - Batch size (Active: 4-10, Distracted: 2-6, Offline: 1-3)
        - Inter-email delay (Active: 20-80s, Distracted: 100-240s, Offline: 300-900s)
        - Cooldown between batches (Active: 10-25min, Distracted: 20-45min, Offline: 1-3hr)
        - Time-of-day awareness (after 6PM → Offline speeds only)
        """
        if is_kill_switch_enabled():
            self._set_campaign_kill_switch_status()
            return 0

        try:
            import random as _rand
            from services.sending_mode_service import (
                get_campaign_batch_params, determine_mode, SendingMode
            )
            
            # Use global campaign logger
            def _log(msg: str):
                _log_activity(campaign.id, msg)
            
            # ── Concurrency guard — only one thread processes a campaign at a time ──
            lock_key = f"campaign:{campaign.id}:processing"
            if not self.queue.redis.set(lock_key, "1", nx=True, ex=60):
                return 0  # Another thread is already processing this campaign
            
            try:
                queued = self._process_campaign_batch_inner(db, campaign, _log, _rand, determine_mode, get_campaign_batch_params, SendingMode)
                return queued or 0
            finally:
                self.queue.redis.delete(lock_key)
        except Exception as e:
            logger.error(f"Campaign {campaign.id} batch error: {e}")
            traceback.print_exc()
            return 0
    
    def _process_campaign_batch_inner(self, db, campaign, _log, _rand, determine_mode, get_campaign_batch_params, SendingMode) -> int:
        """Inner batch processing — called with concurrency lock held.
        
        Per-inbox architecture:
        - Each inbox gets its own mode, batch size, delays, and cooldown
        - Inboxes in cooldown are skipped while others continue
        - Combined schedule is merged and sorted by send time
        """
        try:
            from collections import defaultdict

            def _log_once(tag: str, message: str, ttl_seconds: int = 60):
                """Write an activity line once per short window to avoid log spam."""
                try:
                    dedupe_key = f"campaign:{campaign.id}:activity_once:{tag}"
                    if self.queue.redis.set(dedupe_key, "1", nx=True, ex=ttl_seconds):
                        _log(message)
                except Exception:
                    _log(message)

            cooldown_active_key = f"campaign:{campaign.id}:cooldown:all_inboxes_active"
            cooldown_last_log_key = f"campaign:{campaign.id}:cooldown:all_inboxes_last_log"
            cooldown_reminder_seconds = max(60, int(os.getenv("CAMPAIGN_COOLDOWN_LOG_REMINDER_SECONDS", "300")))

            def _clear_all_inboxes_cooldown_log_state(log_resume: bool = False, ready_inboxes: int = 0):
                """Clear cooldown log markers and optionally emit one resume line."""
                try:
                    had_cooldown_state = bool(self.queue.redis.get(cooldown_active_key))
                    self.queue.redis.delete(cooldown_active_key, cooldown_last_log_key)
                    if had_cooldown_state and log_resume:
                        _log(f"▶️ Cooldown ended. Resuming queueing with {ready_inboxes} ready inbox(es).")
                except Exception:
                    pass

            def _log_all_inboxes_cooldown(remaining_seconds: int):
                """Log cooldown start once, then reminder lines at a coarse interval."""
                remaining = max(0, int(remaining_seconds))
                ttl = max(remaining + cooldown_reminder_seconds + 300, 900)
                now_ts = int(time.time())
                try:
                    just_entered = bool(self.queue.redis.set(cooldown_active_key, "1", nx=True, ex=ttl))
                    self.queue.redis.expire(cooldown_active_key, ttl)

                    should_log = False
                    if just_entered:
                        message = f"⏳ All inboxes are cooling down. Next batch in {remaining}s."
                        should_log = True
                    else:
                        last_logged_raw = self.queue.redis.get(cooldown_last_log_key)
                        last_logged_ts = int(float(last_logged_raw)) if last_logged_raw else 0
                        if now_ts - last_logged_ts >= cooldown_reminder_seconds:
                            message = f"⏳ Cooldown still active. Next batch in {remaining}s."
                            should_log = True

                    if should_log:
                        _log(message)
                        self.queue.redis.set(cooldown_last_log_key, str(now_ts), ex=ttl)
                    else:
                        self.queue.redis.expire(cooldown_last_log_key, ttl)
                except Exception:
                    _log_once(
                        "all_inboxes_cooldown_fallback",
                        f"⏳ All inboxes are cooling down. Next batch in {remaining}s.",
                        ttl_seconds=cooldown_reminder_seconds,
                    )
            
            # ── Early exit: check if there are pending recipients BEFORE expensive work ──
            all_pending_q = db.query(CampaignRecipient).filter(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.status == RecipientSendStatus.PENDING,
                CampaignRecipient.queued_at == None
            )
            total_pending = all_pending_q.count()
            
            if total_pending == 0:
                _clear_all_inboxes_cooldown_log_state(log_resume=False)
                _log_once(
                    "no_pending",
                    "✅ No pending recipients left to queue. Waiting for in-flight sends to finish.",
                    ttl_seconds=120,
                )
                self.queue.redis.set(
                    f"campaign:{campaign.id}:status",
                    json.dumps({"state": "idle", "message": "All emails queued, waiting for sends to complete"}),
                    ex=300
                )
                return 0
            
            # ── Load inbox metadata and compute per-inbox modes ──
            inbox_map = {}
            for inbox in db.query(Inbox).filter(Inbox.id.in_(campaign.inbox_ids)).all():
                inbox_map[inbox.id] = inbox

            smtp_account_map = {}
            smtp_account_ids = [i.smtp_account_id for i in inbox_map.values() if i.smtp_account_id]
            if smtp_account_ids:
                for acct in db.query(SMTPAccount).filter(SMTPAccount.id.in_(smtp_account_ids)).all():
                    smtp_account_map[acct.id] = acct
            
            inbox_modes = {}  # inbox_id → (SendingMode, reason)
            for inbox_id, inbox in inbox_map.items():
                smtp_acct = smtp_account_map.get(inbox.smtp_account_id)
                provider = getattr(smtp_acct, 'provider_type', 'smtp') or 'smtp'
                m, r = determine_mode(
                    provider_type=provider,
                    current_daily_count=inbox.current_daily_count or 0,
                    daily_cap=inbox.daily_cap or 0,
                    inbox_state=inbox.state.value if inbox.state else "not_started",
                    warmup_day=inbox.warmup_day or 0,
                    timezone_name=self.worker_timezone,
                )
                inbox_modes[inbox_id] = (m, r)
            
            # ── Check per-inbox cooldowns — only process inboxes whose cooldown expired ──
            ready_inbox_ids = []
            all_cooldown_ends = []
            for inbox_id in inbox_map:
                cd_key = f"campaign:{campaign.id}:inbox:{inbox_id}:next_batch"
                cd_val = self.queue.redis.get(cd_key)
                if cd_val:
                    remaining = float(cd_val) - time.time()
                    if remaining > 0:
                        all_cooldown_ends.append(float(cd_val))
                        continue  # This inbox is still cooling down
                ready_inbox_ids.append(inbox_id)
            
            if not ready_inbox_ids:
                # All inboxes are in cooldown — report the earliest expiry
                if all_cooldown_ends:
                    earliest = min(all_cooldown_ends)
                    remaining = int(earliest - time.time())
                    _log_all_inboxes_cooldown(remaining)
                    self.queue.redis.set(
                        f"campaign:{campaign.id}:status",
                        json.dumps({"state": "cooldown", "remaining": max(0, remaining)}),
                        ex=max(remaining, 10) + 10
                    )
                return 0

            _clear_all_inboxes_cooldown_log_state(log_resume=True, ready_inboxes=len(ready_inbox_ids))
            
            # ── Group pending recipients by inbox (only ready inboxes) ──
            pending_by_inbox = defaultdict(list)
            ready_pending = db.query(CampaignRecipient).filter(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.status == RecipientSendStatus.PENDING,
                CampaignRecipient.queued_at == None,
                CampaignRecipient.inbox_id.in_(ready_inbox_ids)
            ).order_by(CampaignRecipient.id).all()
            
            for cr in ready_pending:
                pending_by_inbox[cr.inbox_id].append(cr)
            
            if not pending_by_inbox:
                if all_cooldown_ends:
                    earliest = min(all_cooldown_ends)
                    remaining = int(earliest - time.time())
                    self.queue.redis.set(
                        f"campaign:{campaign.id}:status",
                        json.dumps({"state": "cooldown", "remaining": max(0, remaining)}),
                        ex=max(remaining, 10) + 10
                    )
                    _log_once(
                        "pending_waiting_cooldown",
                        f"⏳ Waiting for cooldown before queuing remaining recipients ({max(0, remaining)}s).",
                        ttl_seconds=30,
                    )
                else:
                    self.queue.redis.set(
                        f"campaign:{campaign.id}:status",
                        json.dumps({"state": "idle", "message": "No ready recipients matched active inboxes yet"}),
                        ex=120
                    )
                    _log_once(
                        "no_ready_pending",
                        f"ℹ️ No ready recipients found for active inboxes. Pending total: {total_pending}.",
                        ttl_seconds=60,
                    )
                return 0  # No pending for any ready inbox
            
            # ── Per-inbox: determine batch params, draw micro-batch, compute delays ──
            combined_emails = []  # List of (send_at, campaign_recipient, inbox, delay, inbox_mode)
            per_inbox_info = []   # For logging
            per_inbox_last_delay = {}   # inbox_id → last email delay in seconds
            per_inbox_cooldown = {}     # inbox_id → cooldown seconds
            per_inbox_batch_params = {} # inbox_id → batch_params dict
            
            for inbox_id, inbox_pending in pending_by_inbox.items():
                if not inbox_pending:
                    continue
                
                mode, reason = inbox_modes.get(inbox_id, (SendingMode.ACTIVE, "unknown"))
                batch_params = get_campaign_batch_params(mode)
                per_inbox_batch_params[inbox_id] = batch_params
                
                micro_batch_size = batch_params["micro_batch_size"]
                inter_delay_range = batch_params["inter_email_delay_range"]
                cooldown_range = batch_params["cooldown_range"]
                
                # Draw this inbox's micro-batch
                batch = inbox_pending[:micro_batch_size]
                
                inbox_obj = inbox_map[inbox_id]
                inbox_label = inbox_obj.email if inbox_obj else f"inbox-{inbox_id}"
                
                _log(f"📡 {inbox_label}: {mode.value.upper()} | batch={len(batch)}/{len(inbox_pending)} pending | delay={inter_delay_range[0]}-{inter_delay_range[1]}s | cooldown={cooldown_range[0]//60}-{cooldown_range[1]//60}min ({reason})")
                per_inbox_info.append({"id": inbox_id, "email": inbox_label, "mode": mode.value, "batch_size": len(batch)})
                
                # Compute staggered delays for this inbox's emails
                initial_delay = _rand.randint(max(5, inter_delay_range[0] // 4), inter_delay_range[0])
                delays = [initial_delay]
                for i in range(1, len(batch)):
                    delays.append(delays[-1] + _rand.randint(*inter_delay_range))
                
                per_inbox_last_delay[inbox_id] = delays[-1] if delays else 0
                per_inbox_cooldown[inbox_id] = _rand.randint(*cooldown_range)
                
                for i, cr in enumerate(batch):
                    combined_emails.append({
                        "cr": cr,
                        "inbox_id": inbox_id,
                        "delay": delays[i],
                        "mode": mode,
                    })
            
            if not combined_emails:
                return 0
            
            # ── Sort combined emails by delay so the schedule is chronological ──
            combined_emails.sort(key=lambda x: x["delay"])
            
            # ── Atomic claim ────────────────────────────────────────────────
            # Reserve exactly this micro-batch's recipients with a single
            # UPDATE ... RETURNING so two worker processes/threads can never
            # queue the same recipient, even when both draw from the same
            # pending set. Rows already claimed by another process are simply
            # dropped from this batch.
            claim_ids = [entry["cr"].id for entry in combined_emails]
            claimed_ids = set(self._atomic_claim_recipient_ids(db, claim_ids))
            if claim_ids and not claimed_ids:
                # Every recipient in this micro-batch was claimed by
                # another process first — nothing to queue this round.
                return 0
            if claimed_ids:
                combined_emails = [
                    entry for entry in combined_emails
                    if entry["cr"].id in claimed_ids
                ]
            
            total_batch_size = len(combined_emails)
            _log(f"📦 Combined micro-batch: {total_batch_size} emails across {len(pending_by_inbox)} inboxes ({total_pending} total remaining)")
            
            # Update status
            # Use the best (fastest) mode for display since that's what drives the pace
            mode_priority = {SendingMode.OFFLINE: 0, SendingMode.DISTRACTED: 1, SendingMode.ACTIVE: 2, SendingMode.HYPER: 3}
            best_mode = max(
                [inbox_modes[iid][0] for iid in pending_by_inbox],
                key=lambda m: mode_priority[m]
            )
            self.queue.redis.set(
                f"campaign:{campaign.id}:status",
                json.dumps({"state": "sending", "batch_size": total_batch_size, "remaining": total_pending, "mode": best_mode.value}),
                ex=600
            )
            
            # ── Queue each email ──
            uses_rotation = bool(campaign.template_ids and campaign.template_rotation_state)
            schedule_entries = []
            queued_count = 0
            campaign_owner_tz = self._normalize_timezone_name(campaign.send_timezone)
            if not campaign_owner_tz:
                campaign_owner_tz = self._normalize_timezone_name(self.worker_timezone)
            quota_tz_date = self._tz_date_for(campaign_owner_tz)
            domain_block_reason = None
            blocked_domain = None
            blocked_detail = None
            queued_recipient_ids = set()
            
            for entry in combined_emails:
                cr = entry["cr"]
                inbox_id = entry["inbox_id"]
                cumulative_delay = entry["delay"]
                inbox_mode = entry["mode"]
                
                recipient = db.query(Recipient).filter(Recipient.id == cr.recipient_id).first()
                if not recipient:
                    cr.status = RecipientSendStatus.FAILED
                    cr.error_message = "Recipient not found"
                    continue
                
                inbox = inbox_map.get(inbox_id)
                if not inbox:
                    cr.status = RecipientSendStatus.FAILED
                    cr.error_message = "Inbox not found"
                    continue
                
                # Determine subject/html/text
                # A one-template campaign uses the campaign's copied content,
                # but still needs the source template identity for results.
                used_template_id = (
                    campaign.template_ids[0]
                    if campaign.template_ids and len(campaign.template_ids) == 1
                    else None
                )
                tpl = None
                if uses_rotation:
                    tpl = self._get_rotation_template(db, campaign)
                    if tpl:
                        email_subject = tpl["subject"]
                        email_html = tpl["html"]
                        email_text = tpl["text"]
                        email_attachments = tpl["attachments"]
                        used_template_id = tpl["template_id"]
                    else:
                        email_subject = campaign.subject
                        email_html = campaign.body_html
                        email_text = campaign.body_text
                        email_attachments = campaign.attachments
                else:
                    email_subject = campaign.subject
                    email_html = campaign.body_html
                    email_text = campaign.body_text
                    email_attachments = campaign.attachments
                
                # Auto-detect plain-text body and convert \n → <br>
                import re as _html_re
                if email_html and not _html_re.search(r'<\s*(p|div|br|table|html|head|body|span|a|img|ul|ol|li|h[1-6])\b', email_html, _html_re.IGNORECASE):
                    from html import escape as _esc
                    safe = _esc(email_html)
                    email_html = safe.replace('\n', '<br>\n')
                
                # Render template with recipient data
                recipient_data = {
                    "id": recipient.id,
                    "first_name": recipient.first_name or "",
                    "last_name": recipient.last_name or "",
                    "email": recipient.email,
                    "company": recipient.company or "",
                    "title": recipient.title or "",
                    "unsubscribe_token": recipient.unsubscribe_token or "",
                }
                
                # Build extra context: merge global template_data with per-template overrides
                extra_ctx = None
                if campaign.template_data:
                    extra_ctx = dict(campaign.template_data)
                    per_tpl = extra_ctx.pop('__per_template__', None)
                    if per_tpl and used_template_id:
                        tpl_overrides = per_tpl.get(str(used_template_id), {})
                        if tpl_overrides:
                            extra_ctx.update(tpl_overrides)

                smtp_account = smtp_account_map.get(inbox.smtp_account_id)
                if not smtp_account:
                    cr.status = RecipientSendStatus.FAILED
                    cr.error_message = f"SMTP account not found for inbox {inbox.id}"
                    db.commit()
                    continue

                sender_context = self.template_engine.resolve_sender_context(
                    recipient=recipient_data,
                    sender_email=smtp_account.from_email,
                    sender_name_template=smtp_account.from_name,
                    extra_context=extra_ctx,
                    fallback_sender_name=(inbox.email.split('@')[0].replace('.', ' ').title() if inbox.email else ''),
                )
                
                rendered_subject = self.template_engine.render(
                    template=email_subject,
                    recipient=recipient_data,
                    sender=sender_context,
                    extra_context=extra_ctx
                )
                
                rendered_html = self.template_engine.render_html(
                    template=email_html,
                    recipient=recipient_data,
                    sender=sender_context,
                    campaign_id=campaign.id,
                    include_tracking=campaign.track_opens,
                    include_unsubscribe=True,
                    extra_context=extra_ctx
                )
                
                rendered_text = None
                if email_text:
                    rendered_text = self.template_engine.render(
                        template=email_text,
                        recipient=recipient_data,
                        sender=sender_context,
                        extra_context=extra_ctx
                    )
                
                # CRITICAL: Set queued_at BEFORE adding to queue
                cr.queued_at = datetime.now(timezone.utc)
                
                # ── Queue-time quota reservation ─────────────────────────────
                # ONE fleet-wide gate for campaign sends: the domain daily
                # budget (DOMAIN_DAILY_BUDGET, e.g. 100k/day, UTC rollover).
                # On exhaustion: hold and roll the rest to the next day.
                # Per-inbox warmup caps are enforced by the warmup service
                # (its 30% share) — they do not gate campaign sending.
                quota_domain = (
                    _ds.extract_domain(getattr(smtp_account, "from_email", None))
                    or _ds.extract_domain(inbox.email)
                )

                if quota_domain and _ds.is_throttled(quota_domain):
                    # Domain circuit breaker open — hold this campaign.
                    cr.queued_at = None
                    db.commit()
                    domain_block_reason = "health"
                    blocked_domain = quota_domain
                    blocked_detail = _ds.get_throttle_reason(quota_domain)
                    break

                if quota_domain and _ds.reserve_daily(quota_domain, 1) == 0:
                    cr.queued_at = None
                    db.commit()
                    domain_block_reason = "quota"
                    blocked_domain = quota_domain
                    break
                
                if used_template_id is not None:
                    cr.template_id = used_template_id
                
                # Generate thread ID for conversation tracking
                from services.thread_id import generate_campaign_batch_id, make_thread_id
                batch_id = generate_campaign_batch_id(campaign.id)
                thread_id = make_thread_id(batch_id, recipient.email)
                cr.thread_id = thread_id
                
                if uses_rotation:
                    self._advance_rotation(db, campaign)
                
                db.commit()
                
                # Queue the email
                email_data = {
                    "_id": f"campaign:{campaign.id}:recipient:{recipient.id}",
                    "inbox_id": inbox.id,
                    "to_email": recipient.email,
                    "subject": rendered_subject,
                    "body_html": rendered_html,
                    "body_text": rendered_text,
                    "from_name_override": sender_context.get("from_name"),
                    "reply_to": campaign.reply_to_email,
                    "campaign_id": campaign.id,
                    "recipient_id": recipient.id,
                    "template_id": used_template_id,
                    "thread_id": thread_id,
                    "_max_retries": 3
                }

                if campaign_owner_tz:
                    email_data["_owner_timezone"] = campaign_owner_tz
                
                # Quota metadata so reservations can be released on failure/pause
                if quota_domain:
                    email_data["_quota_domain"] = quota_domain
                    email_data["_quota_domain_date"] = _ds.utc_date()
                email_data["_quota_inbox_id"] = inbox.id
                email_data["_quota_tz"] = campaign_owner_tz
                email_data["_quota_tz_date"] = quota_tz_date
                
                if email_attachments:
                    email_data["attachments"] = email_attachments
                
                self.queue.add(email_data, Priority.NORMAL, delay_seconds=cumulative_delay, user_id=campaign.user_id)
                
                queued_count += 1
                queued_recipient_ids.add(cr.id)
                tpl_label = f" [tpl: {tpl['template_name']}]" if uses_rotation and tpl else ""
                inbox_label = inbox.email.split("@")[0] if inbox else ""
                if cumulative_delay > 0:
                    _log(f"  📧 #{queued_count}/{total_batch_size} → {recipient.email} via {inbox_label}{tpl_label} (delay: {cumulative_delay}s)")
                else:
                    _log(f"  📧 #{queued_count}/{total_batch_size} → {recipient.email} via {inbox_label}{tpl_label} (sending now)")
                
                schedule_entries.append({
                    "email": recipient.email,
                    "send_at": time.time() + cumulative_delay,
                    "idx": queued_count,
                    "total": total_batch_size,
                    "tpl": tpl['template_name'] if uses_rotation and tpl else None,
                    "inbox": inbox.email if inbox else None,
                    "mode": inbox_mode.value,
                })
            
            # A domain block can happen after an atomic micro-batch claim. Any
            # claimed rows that were not actually put in Redis must be released
            # or they remain PENDING with queued_at set forever.
            if domain_block_reason:
                unqueued_claim_ids = [
                    entry["cr"].id
                    for entry in combined_emails
                    if entry["cr"].id not in queued_recipient_ids
                ]
                if unqueued_claim_ids:
                    db.query(CampaignRecipient).filter(
                        CampaignRecipient.id.in_(unqueued_claim_ids),
                        CampaignRecipient.status == RecipientSendStatus.PENDING,
                    ).update({"queued_at": None}, synchronize_session=False)
                    db.commit()

            # ── Set queue wake-up hint ──
            # Tell the worker exactly when the earliest email in this batch needs to be sent
            if schedule_entries:
                earliest_send_at = min(e["send_at"] for e in schedule_entries)
                wake_key = "queue:wake_at"
                existing_wake = self.queue.redis.get(wake_key)
                # Only update if no existing wake-up or this batch has an earlier email
                if not existing_wake or earliest_send_at < float(existing_wake):
                    ttl = max(int(earliest_send_at - time.time()) + 60, 120)
                    self.queue.redis.set(wake_key, str(earliest_send_at), ex=ttl)
            
            # ── Per-inbox cooldowns ──
            # Each inbox gets its own cooldown key based on its mode-derived cooldown range
            global_last_delay = max(per_inbox_last_delay.values()) if per_inbox_last_delay else 0
            global_latest_next_batch = 0
            
            # Count recipients still available for future batches after any
            # blocked atomic claims have been released.
            remaining_after = all_pending_q.count()
            
            for inbox_id in per_inbox_last_delay:
                ib_last_delay = per_inbox_last_delay[inbox_id]
                ib_cooldown = per_inbox_cooldown[inbox_id]
                ib_next_batch = time.time() + ib_last_delay + ib_cooldown
                cd_key = f"campaign:{campaign.id}:inbox:{inbox_id}:next_batch"
                
                if not domain_block_reason and remaining_after > 0:
                    # Only set cooldown if there are more emails to send
                    self.queue.redis.set(cd_key, str(ib_next_batch), ex=int(ib_last_delay + ib_cooldown) + 60)
                    global_latest_next_batch = max(global_latest_next_batch, ib_next_batch)
                else:
                    # Last batch — clear per-inbox cooldowns
                    self.queue.redis.delete(cd_key)
            
            if domain_block_reason:
                # Both health and quota limits reset on the next UTC day.
                roll_seconds = self._seconds_until_next_utc_day()
                self.queue.redis.set(
                    f"campaign:{campaign.id}:next_batch",
                    str(time.time() + roll_seconds),
                    ex=roll_seconds + 3600,
                )

                if domain_block_reason == "health":
                    detail = blocked_detail or "domain health protection active"
                    status_message = f"Domain safety pause — {detail}"
                    log_message = (
                        f"⛔ Domain safety pause for {blocked_domain or 'sending domain'}: {detail} — "
                        f"{remaining_after} remaining roll to tomorrow ({roll_seconds // 3600}h)"
                    )
                else:
                    quota_label = f"{_ds.DOMAIN_DAILY_BUDGET:,}"
                    status_message = f"Daily sending quota ({quota_label}) exhausted"
                    log_message = (
                        f"⛔ Daily sending quota ({quota_label}) exhausted for "
                        f"{blocked_domain or 'sending domain'} — {remaining_after} remaining "
                        f"roll to tomorrow ({roll_seconds // 3600}h)"
                    )

                self.queue.redis.set(
                    f"campaign:{campaign.id}:status",
                    json.dumps({
                        "state": "cooldown",
                        "reason": f"domain_{domain_block_reason}",
                        "message": status_message,
                        "remaining": remaining_after,
                    }),
                    ex=3600,
                )
                _log(log_message)
            elif remaining_after <= 0:
                # === LAST BATCH ===
                _clear_all_inboxes_cooldown_log_state(log_resume=False)
                _log(f"✅ All emails queued! Sends start on schedule (last in {global_last_delay}s). Campaign completes when every email is delivered.")
                
                completion_ttl = global_last_delay + 300
                self.queue.redis.set(
                    f"campaign:{campaign.id}:status",
                    json.dumps({"state": "completed", "remaining": 0}),
                    ex=completion_ttl
                )
                self.queue.redis.set(
                    f"campaign:{campaign.id}:schedule",
                    json.dumps({
                        "phase": "completed",
                        "emails": schedule_entries,
                        "cooldown_until": None,
                        "total_remaining": 0,
                        "inboxes": per_inbox_info,
                    }),
                    ex=completion_ttl
                )
                # Clear legacy campaign-level pacing key
                self.queue.redis.delete(f"campaign:{campaign.id}:next_batch")
                
                logger.info(f"Campaign {campaign.id}: Final batch of {total_batch_size} queued. Last email in {global_last_delay}s.")
            else:
                # === More batches — log per-inbox cooldowns ===
                cooldown_summary_parts = []
                for inbox_id in per_inbox_last_delay:
                    ib_cd = per_inbox_cooldown[inbox_id]
                    ib_last = per_inbox_last_delay[inbox_id]
                    ib_mode = inbox_modes[inbox_id][0].value
                    inbox_label = inbox_map[inbox_id].email.split("@")[0] if inbox_id in inbox_map else str(inbox_id)
                    cooldown_summary_parts.append(f"{inbox_label}[{ib_mode}]={ib_cd//60}m{ib_cd%60}s")
                
                _log(f"⏸ Batch complete. Last email in {global_last_delay}s. Cooldowns: {', '.join(cooldown_summary_parts)}")
                
                cooldown_start = time.time() + global_last_delay
                
                self.queue.redis.set(
                    f"campaign:{campaign.id}:status",
                    json.dumps({"state": "cooldown", "remaining": int(global_latest_next_batch - time.time()), "mode": best_mode.value}),
                    ex=int(global_latest_next_batch - time.time()) + 60
                )
                
                self.queue.redis.set(
                    f"campaign:{campaign.id}:schedule",
                    json.dumps({
                        "phase": "cooldown",
                        "emails": schedule_entries,
                        "cooldown_until": global_latest_next_batch,
                        "cooldown_start": cooldown_start,
                        "total_remaining": remaining_after,
                        "mode": best_mode.value,
                        "inboxes": per_inbox_info,
                    }),
                    ex=int(global_latest_next_batch - time.time()) + 120
                )
                
                # Also set a campaign-level pacing key so the outer check works
                # Use the EARLIEST inbox cooldown so the loop re-enters ASAP
                # (individual inbox cooldowns will gate each inbox independently)
                earliest_next = min(
                    time.time() + per_inbox_last_delay[iid] + per_inbox_cooldown[iid]
                    for iid in per_inbox_last_delay
                )
                pacing_key = f"campaign:{campaign.id}:next_batch"
                self.queue.redis.set(pacing_key, str(earliest_next), ex=int(earliest_next - time.time()) + 60)
                
                logger.info(
                    f"Queued micro-batch of {total_batch_size} for campaign {campaign.id} "
                    f"across {len(per_inbox_last_delay)} inboxes. Cooldowns: {cooldown_summary_parts}"
                    + (f" (rotation active)" if uses_rotation else "")
                )

            return queued_count
            
        except Exception as e:
            logger.error(f"Failed to process campaign batch: {e}")
            try:
                _log_activity(campaign.id, f"❌ ERROR: {e}")
            except Exception:
                pass
            db.rollback()
            return 0

    def process_campaigns_now(self) -> Dict[str, Any]:
        """Queue one immediate micro-batch for all running or due campaigns."""
        results = {
            "queued": 0,
            "campaigns_seen": 0,
            "scheduled_started": 0,
            "errors": [],
        }

        if is_kill_switch_enabled():
            self._set_campaign_kill_switch_status()
            results["errors"].append(KILL_SWITCH_MESSAGE)
            return results

        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)

            running_query = db.query(Campaign).filter(Campaign.status == CampaignStatus.RUNNING)
            if self.worker_timezone:
                running_query = running_query.filter(Campaign.send_timezone == self.worker_timezone)
            running = running_query.all()
            results["campaigns_seen"] = len(running)

            for campaign in running:
                try:
                    results["queued"] += self._process_campaign_batch(db, campaign)
                except Exception as e:
                    results["errors"].append(f"Campaign {campaign.id}: {str(e)}")

            scheduled_query = db.query(Campaign).filter(
                Campaign.status == CampaignStatus.SCHEDULED,
                Campaign.scheduled_at <= now,
            )
            if self.worker_timezone:
                scheduled_query = scheduled_query.filter(Campaign.send_timezone == self.worker_timezone)
            scheduled = scheduled_query.all()

            for campaign in scheduled:
                try:
                    campaign.status = CampaignStatus.RUNNING
                    campaign.started_at = now
                    db.commit()
                    results["scheduled_started"] += 1
                    results["queued"] += self._process_campaign_batch(db, campaign)
                except Exception as e:
                    db.rollback()
                    results["errors"].append(f"Scheduled campaign {campaign.id}: {str(e)}")

            if results["queued"] > 0:
                try:
                    self.queue.redis.set(self.WAKE_KEY, str(time.time()), ex=120)
                except Exception:
                    pass

            return results
        finally:
            db.close()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get worker statistics. Uses Redis heartbeat for cross-process status."""
        queue_stats = self.queue.get_stats()
        
        # Check real worker status via Redis heartbeat (works across processes)
        actually_running = self._running or WorkerService.is_worker_alive()
        
        return {
            "worker": {
                "running": actually_running,
                "num_workers": self.num_workers,
                "started_at": self.stats["started_at"],
                "processed": self.stats["processed"],
                "sent": self.stats["sent"],
                "failed": self.stats["failed"],
            },
            "queue": queue_stats
        }


# Global worker instance
worker_service = WorkerService()


# ==================== Background Scheduler ====================

class BackgroundScheduler:
    """
    Background scheduler for periodic tasks:
    - Check for new replies via IMAP (every 5 minutes)
    - Auto-reply processing (every 2 minutes)
    - Warmup day updates (every hour)
    - Daily warmup emails (every hour during business hours)
    - Inbox mode updates (every 15 minutes) — health score → sending mode
    - Daily count reset at midnight UTC
    """
    
    DAILY_RESET_REDIS_KEY = "scheduler:last_daily_reset_date"

    def __init__(self):
        self._running = False
        self._thread = None
        self._last_imap_check = None
        self._last_auto_reply_check = None
        self._last_warmup_update = None
        self._last_daily_warmup = None
        self._last_mode_update = None
        self._last_domain_health_check = None

        # Hydrate last reset date from Redis so restarts don't re-zero counts
        self._redis = get_redis_client()
        self._last_daily_reset_date = self._load_last_reset_date()
        
        # Intervals in seconds
        self.IMAP_CHECK_INTERVAL = 300  # 5 minutes - check for new replies
        self.AUTO_REPLY_INTERVAL = 120  # 2 minutes - process pending auto-replies
        self.WARMUP_UPDATE_INTERVAL = 3600  # 1 hour
        self.DAILY_WARMUP_INTERVAL = 3600  # 1 hour
        self.MODE_UPDATE_INTERVAL = 900  # 15 minutes - recalculate inbox modes
        self.DOMAIN_HEALTH_INTERVAL = 3600  # 1 hour - domain circuit breaker
    
    def start(self):
        """Start the background scheduler."""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._thread.start()
        logger.info("Background scheduler started")
    
    def stop(self):
        """Stop the background scheduler."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Background scheduler stopped")
    
    def _scheduler_loop(self):
        """Main scheduler loop - runs periodic tasks."""
        logger.info("Scheduler loop started - IMAP check every 5min, auto-reply process every 2min")
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                
                # Check for new replies via IMAP (every 5 minutes)
                if self._should_run_task(self._last_imap_check, self.IMAP_CHECK_INTERVAL):
                    logger.info("Running IMAP check for new replies...")
                    self._check_campaign_replies()
                    self._last_imap_check = now
                    # Store in Redis for API visibility
                    try:
                        r = get_redis_client()
                        r.set('scheduler:last_imap_check', now.isoformat())
                        r.set('scheduler:imap_interval', str(self.IMAP_CHECK_INTERVAL))
                    except Exception:
                        pass
                
                # Process pending auto-replies (every 2 minutes)
                if self._should_run_task(self._last_auto_reply_check, self.AUTO_REPLY_INTERVAL):
                    logger.info("Processing pending auto-replies...")
                    self._process_auto_replies()
                    self._last_auto_reply_check = now
                
                # Update warmup days (every hour)
                if self._should_run_task(self._last_warmup_update, self.WARMUP_UPDATE_INTERVAL):
                    self._update_warmup_days()
                    self._last_warmup_update = now
                
                # Run daily warmup routine (every hour, will check if needed internally)
                if self._should_run_task(self._last_daily_warmup, self.DAILY_WARMUP_INTERVAL):
                    self._run_daily_warmup()
                    self._last_daily_warmup = now
                
                # Update inbox sending modes (every 15 minutes)
                if self._should_run_task(self._last_mode_update, self.MODE_UPDATE_INTERVAL):
                    self._update_inbox_modes()
                    self._last_mode_update = now
                
                # Evaluate domain health circuit breaker (every hour)
                if self._should_run_task(self._last_domain_health_check, self.DOMAIN_HEALTH_INTERVAL):
                    self._evaluate_domain_health()
                    self._last_domain_health_check = now
                
                # Reset daily counts at midnight UTC (check every cycle)
                self._maybe_reset_daily_counts(now)
                
                # Sleep for 30 seconds before next check
                time.sleep(30)
                
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                traceback.print_exc()
                time.sleep(60)
    
    def _should_run_task(self, last_run: Optional[datetime], interval: int) -> bool:
        """Check if a task should run based on last run time and interval."""
        if last_run is None:
            return True
        elapsed = (datetime.now(timezone.utc) - last_run).total_seconds()
        return elapsed >= interval
    
    @staticmethod
    def _strip_reply_prefixes(subject: str) -> str:
        """Strip Re:/Fwd:/Fw: prefixes from a subject."""
        import re as _re
        cleaned = subject.strip()
        while True:
            m = _re.match(r'^(re|fwd|fw)\s*:\s*', cleaned, _re.IGNORECASE)
            if m:
                cleaned = cleaned[m.end():].strip()
            else:
                break
        return cleaned

    @staticmethod
    def _resolve_template_vars(subject: str) -> str:
        """Resolve {{var|default}} template variables to their default values."""
        import re as _re
        resolved = _re.sub(r'\{\{[^|{}]+\|([^}]+)\}\}', r'\1', subject)
        resolved = _re.sub(r'\{\{[^}]+\}\}', '', resolved)
        resolved = _re.sub(r'  +', ' ', resolved).strip()
        return resolved

    @staticmethod
    def _strip_template_vars(subject: str) -> str:
        """Strip ALL template variables (including defaults) from subject."""
        import re as _re
        stripped = _re.sub(r'\{\{[^}]+\}\}', '', subject)
        stripped = _re.sub(r'  +', ' ', stripped).strip()
        return stripped

    def _reply_matches_campaign(self, mail_subject: str, campaign_subject: str,
                                 template_subjects: list[str] | None = None) -> bool:
        """Check if a reply's subject matches the campaign's subject or any template rotation subject.
        
        Uses a structural matching approach: extracts the static text portions
        around template variables and verifies they all appear in order in the reply.
        This handles personalized subjects like 'Welcome to {{company}}, {{name}}!'
        where the reply might be 'Re: Welcome to Acme Corp, John!'.
        """
        import re as _re
        reply_core = self._strip_reply_prefixes(mail_subject).lower().strip()
        
        if not reply_core:
            return True  # Empty reply subject — can't determine, allow match
        
        # Build list of all possible campaign subjects
        all_subjects = [campaign_subject]
        if template_subjects:
            all_subjects.extend(template_subjects)
        
        for subj in all_subjects:
            stripped_subj = self._strip_reply_prefixes(subj)
            
            # Method 1: resolved defaults (old behavior)
            campaign_resolved = self._resolve_template_vars(stripped_subj).lower().strip()
            if campaign_resolved:
                if reply_core == campaign_resolved:
                    return True
                if campaign_resolved in reply_core or reply_core in campaign_resolved:
                    return True
            
            # Method 2: structural matching — extract static text between template vars
            # For "Welcome to {{company|our community}}, {{name|there}}!" → ["welcome to ", ", ", "!"]
            if '{{' in stripped_subj:
                static_parts = _re.split(r'\{\{[^}]*\}\}', stripped_subj)
                static_parts = [p.lower().strip() for p in static_parts if p.strip()]
                
                if not static_parts:
                    return True  # Subject is all template vars — can't determine
                
                # Check if all static parts appear in the reply in order
                search_pos = 0
                all_found = True
                for part in static_parts:
                    idx = reply_core.find(part, search_pos)
                    if idx == -1:
                        all_found = False
                        break
                    search_pos = idx + len(part)
                
                if all_found:
                    return True
            else:
                # No template vars — stripped comparison
                campaign_stripped = stripped_subj.lower().strip()
                if campaign_stripped:
                    if reply_core == campaign_stripped:
                        return True
                    if campaign_stripped in reply_core or reply_core in campaign_stripped:
                        return True
        
        return False

    def _generate_ai_reply(self, first_name: str, sender_name: str, 
                           original_email_subject: str, reply_text: str, 
                           reply_subject: str) -> str:
        """Generate an AI reply using Gemini, with fallback to template."""
        try:
            api_key = os.getenv("GEMINI_API_KEY", "")
            if not api_key or os.getenv("AI_REPLIES_ENABLED", "true").lower() != "true":
                raise ValueError("AI not configured")
            
            from google import genai
            client = genai.Client(api_key=api_key)
            model = os.getenv("AI_MODEL", "gemini-2.0-flash")
            
            prompt = f"""You are replying to an email as {sender_name}. Write a natural, professional reply.

Original email subject: {original_email_subject}
Their reply subject: {reply_subject}
Their message:
{reply_text}

Instructions:
- Address them as {first_name}
- Keep it short (2-4 sentences)
- Be warm and professional
- Continue the conversation naturally
- Do NOT include a subject line, just the body text
- Sign off as {sender_name}
"""
            
            response = client.models.generate_content(
                model=model,
                contents=prompt,
            )
            
            ai_text = response.text.strip() if response.text else ""
            if ai_text and len(ai_text) > 20:
                logger.info(f"AI generated reply for {first_name} ({len(ai_text)} chars)")
                return ai_text
            else:
                raise ValueError("AI response too short")
                
        except Exception as e:
            logger.warning(f"AI reply generation failed, using template: {e}")
            return f"Hi {first_name},\n\nThank you for your reply. I appreciate you getting back to me and would love to continue our conversation.\n\nBest regards,\n{sender_name}"

    def _process_auto_replies(self):
        """Process pending auto-replies that are due."""
        db = SessionLocal()
        try:
            from models.warmup import CampaignAutoReply, WarmupReply
            from services.queue_service import email_queue, Priority
            
            now = datetime.now(timezone.utc)
            processed = 0
            
            # Process campaign auto-replies
            pending_campaign_replies = db.query(CampaignAutoReply).filter(
                CampaignAutoReply.status == "pending",
                CampaignAutoReply.scheduled_at <= now
            ).limit(50).all()
            
            for reply in pending_campaign_replies:
                try:
                    inbox = db.query(Inbox).filter(Inbox.id == reply.inbox_id).first()
                    use_fallback = False
                    fallback = None

                    if not inbox:
                        # Try fallback
                        from models.warmup import FallbackReplyEmail
                        from models.campaign import Campaign
                        campaign = db.query(Campaign).filter(Campaign.id == reply.campaign_id).first()
                        if campaign:
                            fallback = db.query(FallbackReplyEmail).filter(
                                FallbackReplyEmail.user_id == campaign.user_id,
                                FallbackReplyEmail.is_active == True
                            ).first()
                        if fallback:
                            use_fallback = True
                        else:
                            reply.status = "failed"
                            reply.error_message = "Inbox not found and no fallback email configured"
                            continue
                    else:
                        # Check if SMTP works
                        smtp_acct = db.query(SMTPAccount).filter(SMTPAccount.id == inbox.smtp_account_id).first()
                        if not smtp_acct or not smtp_acct.is_active:
                            from models.warmup import FallbackReplyEmail
                            if inbox.user_id:
                                fallback = db.query(FallbackReplyEmail).filter(
                                    FallbackReplyEmail.user_id == inbox.user_id,
                                    FallbackReplyEmail.is_active == True
                                ).first()
                            if fallback:
                                use_fallback = True
                    
                    reply.status = "queued"
                    db.commit()
                    
                    if use_fallback and fallback:
                        email_data = {
                            "_id": f"auto_reply:{reply.id}",
                            "to_email": reply.to_email,
                            "from_email": fallback.email,
                            "subject": reply.subject,
                            "body_text": reply.body_text,
                            "body_html": reply.body_html,
                            "email_type": "campaign_auto_reply",
                            "auto_reply_id": reply.id,
                            "use_fallback": True,
                            "fallback_email": fallback.email,
                            "fallback_password": fallback.app_password,
                            "fallback_smtp_host": fallback.smtp_host,
                            "fallback_smtp_port": fallback.smtp_port,
                        }
                        # Attach thread_id for auto-reply tracking
                        if reply.thread_id:
                            email_data["thread_id"] = reply.thread_id
                        if inbox:
                            email_data["smtp_account_id"] = inbox.smtp_account_id
                            email_data["inbox_id"] = inbox.id
                    else:
                        email_data = {
                            "_id": f"auto_reply:{reply.id}",
                            "smtp_account_id": inbox.smtp_account_id,
                            "inbox_id": inbox.id,
                            "to_email": reply.to_email,
                            "from_email": reply.from_email,
                            "subject": reply.subject,
                            "body_text": reply.body_text,
                            "body_html": reply.body_html,
                            "email_type": "campaign_auto_reply",
                            "auto_reply_id": reply.id,
                        }
                    
                    # Attach thread_id so the worker embeds it in outgoing headers
                    if reply.thread_id:
                        email_data["thread_id"] = reply.thread_id
                    
                    email_queue.add(email_data, Priority.HIGH)
                    processed += 1
                    
                except Exception as e:
                    reply.status = "failed"
                    reply.error_message = str(e)
                    logger.error(f"Failed to queue auto-reply {reply.id}: {e}")
            
            # Process warmup replies
            pending_warmup_replies = db.query(WarmupReply).filter(
                WarmupReply.status == "pending",
                WarmupReply.scheduled_at <= now
            ).limit(50).all()
            
            for reply in pending_warmup_replies:
                try:
                    inbox = db.query(Inbox).filter(Inbox.id == reply.inbox_id).first()
                    if not inbox:
                        reply.status = "failed"
                        reply.error_message = "Inbox not found"
                        continue
                    
                    from services.warmup_email_generator import generate_warmup_reply
                    reply_content = generate_warmup_reply(
                        original_subject=reply.original_subject,
                        sender_name=inbox.email.split('@')[0]
                    )
                    
                    reply.status = "queued"
                    db.commit()
                    
                    email_data = {
                        "_id": f"warmup_reply:{reply.id}",
                        "smtp_account_id": inbox.smtp_account_id,
                        "inbox_id": inbox.id,
                        "to_email": reply.to_email,
                        "from_email": inbox.email,
                        "subject": reply.subject,
                        "body_text": reply_content["body_text"],
                        "body_html": reply_content["body_html"],
                        "email_type": "warmup_reply",
                    }
                    
                    email_queue.add(email_data, Priority.HIGH)
                    processed += 1
                    
                except Exception as e:
                    reply.status = "failed"
                    reply.error_message = str(e)
                    logger.error(f"Failed to queue warmup reply {reply.id}: {e}")
            
            db.commit()
            
            logger.info(f"Scheduler: Processed {processed} pending auto-replies (campaign: {len(pending_campaign_replies)}, warmup: {len(pending_warmup_replies)})")
                
        except Exception as e:
            logger.error(f"Error processing auto-replies: {e}")
            db.rollback()
        finally:
            db.close()
    
    def _check_campaign_replies(self):
        """Check all running campaigns for new replies via IMAP.
        
        OPTIMIZED: Fetches each IMAP inbox ONCE, then matches emails across all campaigns.
        """
        db = SessionLocal()
        diag_info = []  # Diagnostic info for Redis
        try:
            from models.campaign import Campaign, CampaignStatus, CampaignRecipient, RecipientSendStatus
            from models.recipient import Recipient
            from models.warmup import CampaignAutoReply
            from services.imap_service import IMAPService
            import random
            import json as _json
            from datetime import timedelta
            
            # Get running or completed campaigns from last 30 days
            recent_date = datetime.now(timezone.utc) - timedelta(days=30)
            campaigns = db.query(Campaign).filter(
                Campaign.status.in_([CampaignStatus.RUNNING, CampaignStatus.COMPLETED, CampaignStatus.PAUSED]),
                Campaign.started_at >= recent_date
            ).all()
            
            logger.info(f"IMAP check: Found {len(campaigns)} campaigns to check for replies")
            
            total_replies = 0
            total_scheduled = 0
            
            # ===== Phase 1: Build campaign data and collect unique inboxes =====
            campaign_data_list = []
            all_inbox_ids = set()  # Unique inboxes to fetch (each fetched ONCE)
            
            for campaign in campaigns:
                inbox_ids = campaign.inbox_ids or []
                if not inbox_ids:
                    continue
                
                campaign_recipients = db.query(CampaignRecipient, Recipient).join(
                    Recipient, CampaignRecipient.recipient_id == Recipient.id
                ).filter(
                    CampaignRecipient.campaign_id == campaign.id,
                    CampaignRecipient.status == RecipientSendStatus.SENT
                ).all()
                
                if not campaign_recipients:
                    continue
                
                recipient_map = {}
                for cr, recipient in campaign_recipients:
                    recipient_map[recipient.email.lower()] = {"cr": cr, "recipient": recipient}
                
                # Collect inboxes for this campaign (only IMAP-capable ones)
                inboxes_for_campaign = set()
                for inbox_id in inbox_ids:
                    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
                    if not inbox:
                        continue
                    if inbox.reply_enabled and inbox.has_imap:
                        inboxes_for_campaign.add(inbox.id)
                    if inbox.reply_to_email:
                        reply_to_inbox = db.query(Inbox).filter(
                            Inbox.email == inbox.reply_to_email,
                            Inbox.reply_enabled == True
                        ).first()
                        if reply_to_inbox and reply_to_inbox.has_imap:
                            inboxes_for_campaign.add(reply_to_inbox.id)
                
                if not inboxes_for_campaign:
                    logger.debug(f"  Campaign {campaign.id} ({campaign.name}): no IMAP inboxes, skipping")
                    continue
                
                all_inbox_ids.update(inboxes_for_campaign)
                campaign_data_list.append({
                    "campaign": campaign,
                    "recipient_map": recipient_map,
                    "inbox_ids": inboxes_for_campaign,
                })
            
            logger.info(f"  Unique IMAP inboxes to fetch: {len(all_inbox_ids)} (across {len(campaign_data_list)} campaigns with recipients)")
            
            # ===== Phase 2: Fetch each unique IMAP credential ONCE =====
            # Deduplicate: if multiple inboxes share the same IMAP host+user, fetch once
            imap_cache = {}  # inbox_id -> list of emails
            inbox_cache = {}  # inbox_id -> Inbox object
            credential_cache = {}  # "host:user" -> list of emails (dedup key)
            imap_creds_cache = {}  # "host:user" -> {imap_host, imap_port, imap_username, imap_password}
            credential_map = {}   # inbox_id -> cred_key
            
            for inbox_id in all_inbox_ids:
                inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
                if not inbox or not inbox.has_imap:
                    continue
                inbox_cache[inbox_id] = inbox
                
                # Dedup key: same IMAP server + same username = same mailbox
                cred_key = f"{inbox.imap_host}:{inbox.imap_username}".lower()
                credential_map[inbox_id] = cred_key
                
                # Store credentials for later body-fetch
                if cred_key not in imap_creds_cache:
                    imap_creds_cache[cred_key] = {
                        "imap_host": inbox.imap_host,
                        "imap_port": inbox.imap_port,
                        "imap_username": inbox.imap_username,
                        "imap_password": inbox.imap_password,
                    }
                
                if cred_key in credential_cache:
                    # Already fetched this mailbox — reuse cached emails
                    imap_cache[inbox_id] = credential_cache[cred_key]
                    logger.info(f"  Inbox {inbox_id} ({inbox.email}) — reusing cached fetch from {inbox.imap_host} ({inbox.imap_username})")
                    continue
                
                try:
                    imap = IMAPService(
                        inbox.imap_host,
                        inbox.imap_port,
                        inbox.imap_username,
                        inbox.imap_password,
                        True
                    )
                    
                    if not imap.connect(timeout=15):
                        logger.warning(f"  Inbox {inbox_id} ({inbox.email}): IMAP connect FAILED to {inbox.imap_host}")
                        diag_info.append({"inbox": inbox.email, "error": f"IMAP connect failed"})
                        imap_cache[inbox_id] = []
                        credential_cache[cred_key] = []
                        continue
                    
                    # Use fast header-only fetch (10-20x faster than full RFC822)
                    emails = imap.fetch_reply_headers(folder="INBOX", since_hours=24, limit=500)
                    imap.disconnect()
                    imap_cache[inbox_id] = emails
                    credential_cache[cred_key] = emails
                    
                    reply_count = sum(1 for m in emails if m.get("is_reply"))
                    logger.info(f"  Inbox {inbox_id} ({inbox.email}) via {inbox.imap_host}: {len(emails)} headers, {reply_count} replies")
                    diag_info.append({
                        "inbox": inbox.email,
                        "imap_host": inbox.imap_host,
                        "total_emails": len(emails),
                        "reply_emails": reply_count,
                    })
                    
                except Exception as e:
                    logger.error(f"  IMAP error for inbox {inbox_id}: {e}")
                    imap_cache[inbox_id] = []
                    credential_cache[cred_key] = []
                    diag_info.append({"inbox_id": inbox_id, "error": str(e)})
            
            # ===== Build set of ALL system inbox emails to filter out warmup/internal replies =====
            all_system_inbox_emails = set()
            all_system_inboxes = db.query(Inbox).filter(Inbox.is_active == True).all()
            for _sys_inbox in all_system_inboxes:
                all_system_inbox_emails.add(_sys_inbox.email.lower())
            logger.info(f"  Internal/warmup filter: {len(all_system_inbox_emails)} system inbox emails to exclude")
            
            # ===== Phase 3: Match replies using In-Reply-To (deterministic) + thread ID + subject fallback =====
            # Build a global lookup: sent_message_id → (campaign, cr, recipient)
            # This allows O(1) matching by In-Reply-To header.
            message_id_lookup = {}  # sent_message_id → {"campaign": ..., "cr": ..., "recipient": ...}
            thread_id_lookup = {}   # thread_id (base MMMMMMM-CCCCCCCC) → {"campaign": ..., "cr": ..., "recipient": ...}
            campaigns_with_msg_ids = set()  # track which campaigns have stored message IDs
            
            for cdata in campaign_data_list:
                campaign = cdata["campaign"]
                for email_lower, data in cdata["recipient_map"].items():
                    cr = data["cr"]
                    if cr.sent_message_id:
                        campaigns_with_msg_ids.add(campaign.id)
                        message_id_lookup[cr.sent_message_id] = {
                            "campaign": campaign,
                            "cr": cr,
                            "recipient": data["recipient"],
                            "cdata": cdata,
                        }
                    # Also build thread_id lookup for thread-based matching
                    if cr.thread_id:
                        thread_id_lookup[cr.thread_id] = {
                            "campaign": campaign,
                            "cr": cr,
                            "recipient": data["recipient"],
                            "cdata": cdata,
                        }
            
            # Collect all reply emails from all inboxes (deduped), filtering out warmup/internal
            all_reply_mails = []  # (mail, inbox_id, inbox)
            seen_mail_ids = set()
            warmup_skipped = 0
            for cdata in campaign_data_list:
                for inbox_id in cdata["inbox_ids"]:
                    inbox = inbox_cache.get(inbox_id)
                    if not inbox:
                        continue
                    for mail in imap_cache.get(inbox_id, []):
                        if not mail.get("is_reply"):
                            continue
                        # Skip warmup/internal replies (from_email belongs to a system inbox)
                        from_email_check = mail.get("from_email", "").lower()
                        if from_email_check in all_system_inbox_emails:
                            warmup_skipped += 1
                            continue
                        # Dedup by Message-ID so each reply is processed once
                        mid = mail.get("message_id", "")
                        dedup_key = mid if mid else f"{mail.get('from_email','')}|{mail.get('subject','')}|{mail.get('date','')}"
                        if dedup_key in seen_mail_ids:
                            continue
                        seen_mail_ids.add(dedup_key)
                        all_reply_mails.append((mail, inbox_id, inbox))
            
            if warmup_skipped > 0:
                logger.info(f"  Filtered out {warmup_skipped} warmup/internal replies")
            
            # --- Pass 1: Deterministic In-Reply-To + Thread ID matching ---
            matched_mail_ids = set()  # track mails already matched by In-Reply-To/thread
            from services.thread_id import extract_thread_id_from_references, parse_thread_id
            for mail, inbox_id, inbox in all_reply_mails:
                in_reply_to = (mail.get("in_reply_to") or "").strip()
                references = (mail.get("references") or "").strip()
                
                # Try In-Reply-To first, then check References
                candidate_ids = []
                if in_reply_to:
                    candidate_ids.append(in_reply_to)
                if references:
                    # References header can contain multiple message IDs
                    candidate_ids.extend(references.split())
                
                matched_entry = None
                for cid in candidate_ids:
                    cid = cid.strip()
                    if cid in message_id_lookup:
                        matched_entry = message_id_lookup[cid]
                        break
                
                # If no In-Reply-To match, try thread ID matching
                if not matched_entry:
                    # Try X-Thread-ID header first
                    x_tid = mail.get("x_thread_id", "")
                    if x_tid:
                        parsed = parse_thread_id(x_tid)
                        if parsed and parsed["base"] in thread_id_lookup:
                            matched_entry = thread_id_lookup[parsed["base"]]
                    
                    # Try extracting thread ID from In-Reply-To/References message IDs
                    if not matched_entry:
                        combined_refs = f"{in_reply_to} {references}".strip()
                        extracted_tid = extract_thread_id_from_references(combined_refs)
                        if extracted_tid:
                            parsed = parse_thread_id(extracted_tid)
                            if parsed and parsed["base"] in thread_id_lookup:
                                matched_entry = thread_id_lookup[parsed["base"]]
                
                if not matched_entry:
                    continue
                
                campaign = matched_entry["campaign"]
                cr = matched_entry["cr"]
                recipient = matched_entry["recipient"]
                from_email = mail.get("from_email", "").lower()
                mail_subject = mail.get("subject", "")
                mail_message_id = mail.get("message_id", "")
                
                # Verify the from_email matches the recipient (sanity check)
                if from_email != recipient.email.lower():
                    continue
                
                # Dedup: check if this exact email was already processed (by IMAP Message-ID)
                existing = None
                if mail_message_id:
                    existing = db.query(CampaignAutoReply).filter(
                        CampaignAutoReply.original_message_id == mail_message_id,
                    ).first()
                
                if existing:
                    matched_mail_ids.add(mail.get("message_id", "") or f"{from_email}|{mail_subject}")
                    continue
                
                logger.info(f"    *** REPLY DETECTED (In-Reply-To/thread match) from {from_email} for campaign '{campaign.name}': '{mail_subject}'")
                matched_mail_ids.add(mail.get("message_id", "") or f"{from_email}|{mail_subject}")
                total_replies += 1
                
                # Mark reply on campaign + recipient
                campaign.total_replies = (campaign.total_replies or 0) + 1
                if not cr.replied_at:
                    cr.replied_at = datetime.now(timezone.utc)
                
                # Update inbox reply metrics
                inbox_obj = inbox_cache.get(cr.inbox_id) or inbox
                inbox_obj.total_replied = (inbox_obj.total_replied or 0) + 1
                inbox_obj.total_received = (inbox_obj.total_received or 0) + 1
                
                # Fetch full body
                reply_body_text = ""
                reply_body_html = ""
                mail_uid = mail.get("uid", "")
                if mail_uid:
                    _ckey = credential_map.get(inbox_id, "")
                    _creds = imap_creds_cache.get(_ckey, {})
                    if _creds:
                        try:
                            _body_imap = IMAPService(_creds["imap_host"], _creds["imap_port"], _creds["imap_username"], _creds["imap_password"], True)
                            if _body_imap.connect(timeout=10):
                                body_result = _body_imap.fetch_email_body(mail_uid)
                                if body_result:
                                    reply_body_text = body_result.get("body_text", "")
                                    reply_body_html = body_result.get("body_html", "")
                                _body_imap.disconnect()
                        except Exception as _be:
                            logger.debug(f"Failed to fetch reply body for uid {mail_uid}: {_be}")
                
                # Schedule auto-reply
                delay_minutes = random.randint(2, 15)
                scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
                
                original_subject = mail.get("subject", campaign.subject)
                subject = original_subject if original_subject.lower().startswith("re:") else f"Re: {original_subject}"
                
                first_name = recipient.first_name or from_email.split("@")[0]
                sender_name = (inbox_obj.email if inbox_obj else inbox.email).split("@")[0].replace(".", " ").title()
                
                reply_snippet = reply_body_text[:500] if reply_body_text else ""
                body_text = self._generate_ai_reply(
                    first_name=first_name,
                    sender_name=sender_name,
                    original_email_subject=campaign.subject,
                    reply_text=reply_snippet,
                    reply_subject=mail_subject,
                )
                
                # Determine thread_id for this reply
                reply_thread_id = None
                if cr.thread_id:
                    # Count existing auto-replies for this thread to get sequence number
                    existing_count = db.query(CampaignAutoReply).filter(
                        CampaignAutoReply.campaign_id == campaign.id,
                        CampaignAutoReply.campaign_recipient_id == cr.id,
                    ).count()
                    from services.thread_id import make_full_thread_id as _make_full
                    reply_thread_id = _make_full(cr.thread_id, existing_count + 1)
                
                auto_reply = CampaignAutoReply(
                    campaign_id=campaign.id,
                    campaign_recipient_id=cr.id,
                    inbox_id=inbox_obj.id if inbox_obj else inbox.id,
                    to_email=from_email,
                    from_email=inbox_obj.email if inbox_obj else inbox.email,
                    subject=subject,
                    original_subject=campaign.subject,
                    body_text=body_text,
                    body_html="<p>" + body_text.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>",
                    original_reply_subject=mail_subject,
                    original_reply_snippet=reply_body_text[:500] if reply_body_text else None,
                    scheduled_at=scheduled_at,
                    status="pending",
                    original_message_id=mail_message_id,
                    thread_id=reply_thread_id,
                )
                
                db.add(auto_reply)
                total_scheduled += 1
                logger.info(f"      Auto-reply scheduled for {from_email} in {delay_minutes}min")
            
            # --- Pass 2: Subject-based fallback for campaigns without stored message IDs ---
            for cdata in campaign_data_list:
                campaign = cdata["campaign"]
                recipient_map = cdata["recipient_map"]
                
                # Skip campaigns that already have message IDs stored — they were handled in Pass 1
                if campaign.id in campaigns_with_msg_ids:
                    continue
                
                # Build the set of FROM addresses this campaign actually sent from
                campaign_sender_emails = set()
                for iid in (campaign.inbox_ids or []):
                    ib = inbox_cache.get(iid)
                    if ib:
                        campaign_sender_emails.add(ib.email.lower())
                
                try:
                    for inbox_id in cdata["inbox_ids"]:
                        inbox = inbox_cache.get(inbox_id)
                        if not inbox:
                            continue
                        emails = imap_cache.get(inbox_id, [])
                        
                        for mail in emails:
                            if not mail.get("is_reply"):
                                continue
                            
                            # Skip warmup/internal replies
                            from_email = mail.get("from_email", "").lower()
                            if from_email in all_system_inbox_emails:
                                continue
                            
                            # Skip if already matched by In-Reply-To in Pass 1
                            mid = mail.get("message_id", "")
                            _dedup_key = mid if mid else f"{mail.get('from_email','')}|{mail.get('subject','')}"
                            if _dedup_key in matched_mail_ids:
                                continue
                            
                            if from_email not in recipient_map:
                                continue
                            
                            # Verify the To: header matches this campaign's sender
                            to_raw = mail.get("to", "").lower()
                            to_email = mail.get("to_email", "").lower()
                            if not to_email:
                                continue
                            if not any(addr in to_raw for addr in campaign_sender_emails):
                                continue
                            
                            mail_subject = mail.get("subject", "")
                            # Load template rotation subjects for matching
                            _template_subjects = []
                            if campaign.template_ids:
                                from models.ses_template import SESEmailTemplate
                                _template_subjects = [t.subject_line for t in db.query(SESEmailTemplate.subject_line).filter(
                                    SESEmailTemplate.id.in_(campaign.template_ids)).all() if t.subject_line]
                            if not self._reply_matches_campaign(mail_subject, campaign.subject or "", _template_subjects):
                                continue
                            
                            data = recipient_map[from_email]
                            cr = data["cr"]
                            recipient = data["recipient"]
                            mail_message_id = mail.get("message_id", "")
                            
                            # Dedup check: only by original_message_id (allows multiple replies per thread)
                            existing = None
                            if mail_message_id:
                                existing = db.query(CampaignAutoReply).filter(
                                    CampaignAutoReply.original_message_id == mail_message_id,
                                ).first()
                            
                            if existing:
                                continue
                            
                            logger.info(f"    *** REPLY DETECTED (subject fallback) from {from_email} for campaign '{campaign.name}': '{mail_subject}'")
                            total_replies += 1
                            
                            # Always count each unique reply; mark first reply timestamp on recipient
                            campaign.total_replies = (campaign.total_replies or 0) + 1
                            if not cr.replied_at:
                                cr.replied_at = datetime.now(timezone.utc)
                            
                            # Update inbox reply metrics (feeds health score)
                            inbox.total_replied = (inbox.total_replied or 0) + 1
                            inbox.total_received = (inbox.total_received or 0) + 1
                            
                            # ── Fetch full body for matched reply (headers don't include body) ──
                            reply_body_text = ""
                            reply_body_html = ""
                            mail_uid = mail.get("uid", "")
                            if mail_uid:
                                _ckey = credential_map.get(inbox_id, "")
                                _creds = imap_creds_cache.get(_ckey, {})
                                if _creds:
                                    try:
                                        _body_imap = IMAPService(_creds["imap_host"], _creds["imap_port"], _creds["imap_username"], _creds["imap_password"], True)
                                        if _body_imap.connect(timeout=10):
                                            body_result = _body_imap.fetch_email_body(mail_uid)
                                            if body_result:
                                                reply_body_text = body_result.get("body_text", "")
                                                reply_body_html = body_result.get("body_html", "")
                                            _body_imap.disconnect()
                                    except Exception as _be:
                                        logger.debug(f"Failed to fetch reply body for uid {mail_uid}: {_be}")
                            
                            delay_minutes = random.randint(2, 15)
                            scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
                            
                            original_subject = mail.get("subject", campaign.subject)
                            subject = original_subject if original_subject.lower().startswith("re:") else f"Re: {original_subject}"
                            
                            first_name = recipient.first_name or from_email.split("@")[0]
                            sender_name = inbox.email.split("@")[0].replace(".", " ").title()
                            
                            reply_snippet = reply_body_text[:500] if reply_body_text else ""
                            body_text = self._generate_ai_reply(
                                first_name=first_name,
                                sender_name=sender_name,
                                original_email_subject=campaign.subject,
                                reply_text=reply_snippet,
                                reply_subject=mail_subject,
                            )
                            
                            # Determine thread_id for this reply  
                            reply_thread_id_p2 = None
                            if cr.thread_id:
                                existing_count_p2 = db.query(CampaignAutoReply).filter(
                                    CampaignAutoReply.campaign_id == campaign.id,
                                    CampaignAutoReply.campaign_recipient_id == cr.id,
                                ).count()
                                from services.thread_id import make_full_thread_id as _mft
                                reply_thread_id_p2 = _mft(cr.thread_id, existing_count_p2 + 1)
                            
                            auto_reply = CampaignAutoReply(
                                campaign_id=campaign.id,
                                campaign_recipient_id=cr.id,
                                inbox_id=inbox.id,
                                to_email=from_email,
                                from_email=inbox.email,
                                subject=subject,
                                original_subject=campaign.subject,
                                body_text=body_text,
                                body_html="<p>" + body_text.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>",
                                original_reply_subject=mail_subject,
                                original_reply_snippet=reply_body_text[:500] if reply_body_text else None,
                                scheduled_at=scheduled_at,
                                status="pending",
                                original_message_id=mail_message_id,
                                thread_id=reply_thread_id_p2,
                            )
                            
                            db.add(auto_reply)
                            total_scheduled += 1
                            logger.info(f"      Auto-reply scheduled for {from_email} in {delay_minutes}min")
                    
                    db.commit()
                    
                except Exception as e:
                    logger.error(f"Error checking replies for campaign {campaign.id}: {e}")
                    traceback.print_exc()
                    db.rollback()
            
            # Store diagnostic info in Redis for API visibility
            try:
                r = get_redis_client()
                r.set('imap_check:last_diagnostic', _json.dumps({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "campaigns_checked": len(campaigns),
                    "total_replies_found": total_replies,
                    "total_scheduled": total_scheduled,
                    "details": diag_info,
                }), ex=3600)
            except Exception:
                pass
            
            logger.info(f"Scheduler: IMAP check found {total_replies} new replies, scheduled {total_scheduled} auto-replies")
            
        except Exception as e:
            logger.error(f"Error in IMAP check: {e}")
            db.rollback()
        finally:
            db.close()
    
    # ── Redis-backed daily reset helpers ──────────────────────────────

    def _load_last_reset_date(self):
        """Read the last reset date from Redis (survives worker restarts)."""
        try:
            val = self._redis.get(self.DAILY_RESET_REDIS_KEY)
            if val:
                from datetime import date as _date
                parts = val.split("-")
                loaded = _date(int(parts[0]), int(parts[1]), int(parts[2]))
                logger.info(f"Scheduler: Loaded last daily-reset date from Redis: {loaded}")
                return loaded
        except Exception as e:
            logger.warning(f"Scheduler: Could not load last reset date from Redis: {e}")
        return None

    def _persist_reset_date(self, d):
        """Write the reset date to Redis so the next restart picks it up."""
        try:
            # Expire after 48h — stale values are harmless (just triggers one
            # extra reset on the next day), but avoids leaving orphan keys.
            self._redis.set(self.DAILY_RESET_REDIS_KEY, d.isoformat(), ex=172800)
        except Exception as e:
            logger.warning(f"Scheduler: Could not persist reset date to Redis: {e}")

    def _maybe_reset_daily_counts(self, now: datetime):
        """Reset current_daily_count for ALL active inboxes once per UTC day.
        
        The guard is kept both in-memory *and* in Redis so that:
        - Normal ticks are free (in-memory check).
        - Worker restarts don't re-zero today's counts (Redis check).
        """
        today = now.date()
        if self._last_daily_reset_date == today:
            return  # Fast path — already reset this process lifetime
        
        db = SessionLocal()
        try:
            from models.inbox import Inbox
            
            # Reset daily count for ALL active inboxes
            reset_count = db.query(Inbox).filter(
                Inbox.is_active == True,
                Inbox.current_daily_count > 0,
            ).update({Inbox.current_daily_count: 0}, synchronize_session='fetch')
            
            db.commit()
            self._last_daily_reset_date = today
            self._persist_reset_date(today)
            
            if reset_count > 0:
                logger.info(f"Scheduler: Daily reset — cleared current_daily_count for {reset_count} inboxes")
            else:
                logger.info("Scheduler: Daily reset — no inboxes needed clearing")
            
        except Exception as e:
            logger.error(f"Error resetting daily counts: {e}")
            db.rollback()
        finally:
            db.close()
    
    def _evaluate_domain_health(self):
        """Domain circuit breaker: throttle domains with unhealthy bounce or
        complaint rates (computed from today's Redis health stats)."""
        try:
            for domain in _ds.get_active_domains():
                throttled = _ds.evaluate_domain_health(domain)
                if throttled:
                    logger.warning(
                        f"Scheduler: domain {domain} throttled by circuit breaker"
                    )
        except Exception as e:
            logger.error(f"Error evaluating domain health: {e}")

    def _update_warmup_days(self):
        """Update warmup day for all warming inboxes.
        Also fixes daily_cap for warmed-up inboxes that have stale caps."""
        db = SessionLocal()
        try:
            from models.inbox import Inbox, InboxState
            from services.warmup_service import WarmupService
            import os as _os
            
            WARMUP_END_VOLUME = int(_os.getenv("WARMUP_END_VOLUME", 100))
            service = WarmupService(db)
            
            # Get all inboxes in warming up state
            warming_inboxes = db.query(Inbox).filter(
                Inbox.state == InboxState.WARMING_UP
            ).all()
            
            updated = 0
            for inbox in warming_inboxes:
                try:
                    result = service.update_warmup_day(inbox)
                    if result.get("success"):
                        updated += 1
                except Exception as e:
                    logger.error(f"Failed to update warmup day for {inbox.email}: {e}")
            
            if updated > 0:
                logger.info(f"Scheduler: Updated warmup day for {updated} inboxes")
            
            # Fix warmed-up inboxes whose daily_cap is stuck below WARMUP_END_VOLUME
            warmed_inboxes = db.query(Inbox).filter(
                Inbox.state == InboxState.WARMED_UP,
                Inbox.is_active == True,
                Inbox.daily_cap < WARMUP_END_VOLUME,
            ).all()
            
            fixed = 0
            for inbox in warmed_inboxes:
                old_cap = inbox.daily_cap
                inbox.daily_cap = WARMUP_END_VOLUME
                fixed += 1
                logger.info(f"Scheduler: Fixed daily_cap for warmed-up inbox {inbox.email}: {old_cap} → {WARMUP_END_VOLUME}")
            
            if fixed > 0:
                db.commit()
                logger.info(f"Scheduler: Fixed {fixed} warmed-up inboxes with stale daily caps")
                
        except Exception as e:
            logger.error(f"Error updating warmup days: {e}")
        finally:
            db.close()
    
    def _run_daily_warmup(self):
        """Run daily warmup routine - assign partners if needed, then queue warmup emails."""
        db = SessionLocal()
        try:
            from services.warmup_service import WarmupService
            from services.reply_service import PartnerService
            from models.warmup import WarmupPartner
            from models.inbox import InboxState
            
            partner_svc = PartnerService(db)
            
            # Auto-assign partners for warming inboxes that have none
            warming_inboxes = db.query(Inbox).filter(
                Inbox.is_active == True,
                Inbox.state == InboxState.WARMING_UP,
            ).all()
            
            for inbox in warming_inboxes:
                active_count = db.query(WarmupPartner).filter(
                    WarmupPartner.inbox_id == inbox.id,
                    WarmupPartner.is_active == True,
                ).count()
                if active_count == 0:
                    assigned = partner_svc.assign_partners(inbox)
                    if assigned:
                        logger.info(f"Scheduler: Auto-assigned {len(assigned)} warmup partners for {inbox.email}")
            
            # Rotate stale partners (every call checks dates internally)
            try:
                rotation = partner_svc.check_and_rotate_all()
                if rotation.get("rotated", 0) > 0:
                    logger.info(f"Scheduler: Rotated partners for {rotation['rotated']} inboxes")
            except Exception as e:
                logger.warning(f"Partner rotation error: {e}")
            
            service = WarmupService(db)
            result = service.run_daily_warmup()
            
            queued = result.get("emails_queued", 0)
            if queued > 0:
                logger.info(f"Scheduler: Queued {queued} warmup emails")
            
            errors = result.get("errors", [])
            for error in errors[:5]:  # Log first 5 errors
                logger.warning(f"Warmup error: {error}")
                
        except Exception as e:
            logger.error(f"Error running daily warmup: {e}")
        finally:
            db.close()
    
    def _update_inbox_modes(self):
        """Recalculate sending modes for all active inboxes.
        
        Runs every 15 minutes. For each active inbox:
        1. Sync DailyMetrics rates → inbox rate columns
        2. Determine the global sending mode based on warmup progression
        3. Persist changes to DB
        4. Store summary in Redis for API/UI visibility
        """
        db = SessionLocal()
        try:
            from services.sending_mode_service import (
                determine_mode, SendingMode
            )
            from services.monitoring_service import MetricsService
            
            metrics_svc = MetricsService(db)
            
            # Get all active inboxes
            active_inboxes = db.query(Inbox).filter(
                Inbox.is_active == True
            ).all()
            
            updated = 0
            mode_summary = {"active": 0, "distracted": 0, "offline": 0}
            transitions = []
            
            now = datetime.now(timezone.utc)
            
            for inbox in active_inboxes:
                try:
                    # 0. Sync DailyMetrics → inbox rate columns (last 7 days)
                    try:
                        summary = metrics_svc.get_inbox_metrics_summary(inbox.id, days=7)
                        if summary and summary.get("has_data"):
                            rates = summary["rates"]
                            # Rates are 0-100 floats; inbox columns store as int * 100
                            inbox.bounce_rate = int(rates.get("bounce_rate", 0) * 100)
                            inbox.open_rate = int(rates.get("open_rate", 0) * 100)
                            inbox.reply_rate = int(rates.get("reply_rate", 0) * 100)
                            # spam_rate comes from complaints, calc separately
                            totals = summary.get("totals", {})
                            total_sent = totals.get("sent", 0)
                            spam_complaints = totals.get("spam_complaints", 0)
                            if total_sent > 0:
                                inbox.spam_rate = int((spam_complaints / total_sent * 100) * 100)
                    except Exception as e:
                        logger.debug(f"Metrics sync failed for inbox {inbox.id}: {e}")
                    
                    # 1. Get provider type
                    smtp_acct = db.query(SMTPAccount).filter(
                        SMTPAccount.id == inbox.smtp_account_id
                    ).first()
                    provider = getattr(smtp_acct, 'provider_type', 'smtp') or 'smtp'
                    
                    # 2. Determine mode
                    new_mode, reason = determine_mode(
                        provider_type=provider,
                        current_daily_count=inbox.current_daily_count or 0,
                        daily_cap=inbox.daily_cap or 0,
                        inbox_state=inbox.state.value if inbox.state else "not_started",
                        warmup_day=inbox.warmup_day or 0,
                        now=now,
                        # Working hours are delivery scheduling concerns, not an
                        # inbox mode constraint. Graduation applies globally.
                        timezone_name=None,
                    )
                    
                    # 3. Check if mode changed (respect lock period)
                    old_mode = inbox.sending_mode
                    mode_changed = old_mode != new_mode
                    
                    # Respect mode lock (prevents rapid flipping)
                    if mode_changed and inbox.mode_locked_until and now < inbox.mode_locked_until:
                        mode_changed = False  # Keep current mode until lock expires
                    
                    # 4. Persist
                    if mode_changed:
                        old_mode_val = old_mode.value if old_mode else "none"
                        inbox.sending_mode = new_mode
                        inbox.last_mode_change = now
                        # Lock mode for 10 minutes to prevent jitter
                        inbox.mode_locked_until = now + timedelta(minutes=10)
                        
                        transitions.append(
                            f"{inbox.email}: {old_mode_val} → {new_mode.value} "
                            f"(score={(inbox.health_score or 0):.0f}, reason={reason})"
                        )
                    
                    mode_summary[new_mode.value if mode_changed else (old_mode.value if old_mode else "offline")] += 1
                    updated += 1
                    
                except Exception as e:
                    logger.error(f"Failed to update mode for inbox {inbox.id} ({inbox.email}): {e}")
            
            db.commit()
            
            # Log transitions
            if transitions:
                logger.info(f"Scheduler: Mode transitions: {transitions}")
            logger.info(
                f"Scheduler: Updated {updated} inbox modes — "
                f"Active: {mode_summary['active']}, "
                f"Distracted: {mode_summary['distracted']}, "
                f"Offline: {mode_summary['offline']}"
            )
            
            # Store summary in Redis for API
            try:
                r = get_redis_client()
                import json as _json
                r.set('scheduler:mode_update', _json.dumps({
                    "timestamp": now.isoformat(),
                    "updated": updated,
                    "summary": mode_summary,
                    "transitions": transitions[-10:],  # Last 10 transitions
                }), ex=3600)
            except Exception:
                pass
                
        except Exception as e:
            logger.error(f"Error updating inbox modes: {e}")
            db.rollback()
        finally:
            db.close()


# Global scheduler instance
background_scheduler = BackgroundScheduler()


def run_worker():
    """Run the worker as a standalone process."""
    
    def signal_handler(signum, frame):
        logger.info("Received shutdown signal")
        background_scheduler.stop()
        worker_service.stop()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start background scheduler
    background_scheduler.start()
    
    # Start worker
    worker_service.start()
    
    # Keep main thread alive
    try:
        while worker_service._running:
            time.sleep(1)
    except KeyboardInterrupt:
        background_scheduler.stop()
        worker_service.stop()


if __name__ == "__main__":
    run_worker()
