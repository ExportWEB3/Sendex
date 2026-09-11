import json
import time
import random
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta, date, timezone
from enum import Enum
import os
from dotenv import load_dotenv
import logging

from services.redis_client import get_redis_client

load_dotenv()

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")


class Priority(str, Enum):
    HIGH = "high"       # Warm-up emails - send first
    NORMAL = "normal"   # Campaign emails
    LOW = "low"         # Retries


class EmailQueue:
    """Redis-based email queue with priority support and per-user tracking"""
    
    # Queue keys
    QUEUE_KEY = "email:queue:{priority}"
    PROCESSING_KEY = "email:processing"
    DEAD_LETTER_KEY = "email:dead_letter"
    STATS_KEY = "email:stats"
    ACTIVE_IDS_KEY = "email:active_ids"  # Set of all active email IDs (in queue or processing)
    
    # Campaign batching work-queue keys — scoped per worker timezone so an
    # ET worker can never batch a PT campaign (and vice versa).
    CAMPAIGN_DUE_KEY = "campaign:due:{tz_tag}"
    CAMPAIGN_WORK_KEY = "campaign:work:{tz_tag}"
    
    # Global daily stats
    DAILY_STATS_KEY = "email:daily:{date}"  # Hash with sent, failed counts per day
    
    # Per-user stats keys
    USER_STATS_KEY = "email:user_stats:{user_id}"  # Hash with sent, failed, queued counts
    USER_DAILY_KEY = "email:user_daily:{user_id}:{date}"  # Daily stats per user
    
    # Per-inbox campaign quota (timezone-keyed so rolls happen on the
    # campaign's local day boundary, not UTC).
    INBOX_QUOTA_KEY = "inbox_quota:{inbox_id}:{tz}:{date}"
    
    # Lua script: atomic due-item pop with optional timezone ownership filter.
    _POP_LUA = """
    local queue_key   = KEYS[1]
    local proc_key    = KEYS[2]
    local now         = tonumber(ARGV[1])
    local worker_tz   = ARGV[2] or ''
    local scan_limit  = tonumber(ARGV[3]) or 200

    if scan_limit < 1 then
        scan_limit = 1
    end

    local items = redis.call('ZRANGEBYSCORE', queue_key, '-inf', now, 'LIMIT', 0, scan_limit)
    if #items == 0 then
        return nil
    end

    for i = 1, #items do
        local raw = items[i]
        local owner_tz = string.match(raw, '"_owner_timezone"%s*:%s*"([^"]+)"')
        if worker_tz == '' or (not owner_tz) or owner_tz == worker_tz then
            redis.call('ZREM', queue_key, raw)

            -- Extract _id from JSON (lightweight pattern match)
            local id = string.match(raw, '"_id"%s*:%s*"([^"]+)"')
            if id then
                redis.call('HSET', proc_key, id, raw)
            end

            return raw
        end
    end

    return nil
    """
    
    def __init__(self):
        self.redis = get_redis_client(REDIS_URL)
        self._pop_script = self.redis.register_script(self._POP_LUA)
    
    def add(
        self,
        email_data: Dict[str, Any],
        priority: Priority = Priority.NORMAL,
        delay_seconds: int = 0,
        user_id: Optional[int] = None
    ) -> str:
        """Add email to queue with priority and user tracking"""
        
        # Keep existing ID if present (for re-queuing), otherwise generate new one
        email_id = email_data.get("_id")
        is_requeue = bool(email_id)
        
        if not email_id:
            email_id = f"{int(time.time() * 1000)}:{email_data.get('to_email', 'unknown')}"
            email_data["_id"] = email_id
            email_data["_queued_at"] = datetime.now(timezone.utc).isoformat()
        
        # Store user_id in email data for tracking
        if user_id:
            email_data["_user_id"] = user_id
        
        # DUPLICATE PREVENTION: Check if this email ID is already active
        if self.redis.sismember(self.ACTIVE_IDS_KEY, email_id):
            logger.warning(f"Email {email_id} already active, skipping duplicate add")
            return email_id
        
        # Add/update metadata
        email_data["_priority"] = priority.value
        email_data["_retry_count"] = email_data.get("_retry_count", 0)
        
        # Calculate score (timestamp for delayed sending)
        score = time.time() + delay_seconds
        
        # Add to active IDs set
        self.redis.sadd(self.ACTIVE_IDS_KEY, email_id)
        
        # Add to priority queue (sorted set)
        queue_key = self.QUEUE_KEY.format(priority=priority.value)
        self.redis.zadd(queue_key, {json.dumps(email_data): score})
        
        # Update stats only for new emails, not re-queues
        if not is_requeue:
            self.redis.hincrby(self.STATS_KEY, "total_queued", 1)
            
            # Update per-user stats
            if user_id:
                user_stats_key = self.USER_STATS_KEY.format(user_id=user_id)
                self.redis.hincrby(user_stats_key, "queued", 1)
                self.redis.hincrby(user_stats_key, "current_queued", 1)
                
                # Daily stats
                today = date.today().isoformat()
                user_daily_key = self.USER_DAILY_KEY.format(user_id=user_id, date=today)
                self.redis.hincrby(user_daily_key, "queued", 1)
                self.redis.expire(user_daily_key, 86400 * 7)  # Keep for 7 days
        
        logger.info(f"Email {'re-' if is_requeue else ''}queued: {email_id} priority={priority.value} user={user_id}")
        return email_id
    
    def _weighted_priority_order(self) -> List[Priority]:
        """Weighted starting order for pops so warmup traffic can't starve
        campaign emails.

        Defaults to ~70% campaign (NORMAL) first, ~30% warmup (HIGH) first.
        Retries (LOW) always trail. Weights are env-configurable.
        """
        w_normal = float(os.getenv("QUEUE_POP_WEIGHT_NORMAL", "0.70"))
        w_high = float(os.getenv("QUEUE_POP_WEIGHT_HIGH", "0.30"))
        total = max(0.0001, w_normal + w_high)
        if random.random() < (w_high / total):
            return [Priority.HIGH, Priority.NORMAL, Priority.LOW]
        return [Priority.NORMAL, Priority.HIGH, Priority.LOW]

    def pop(
        self,
        priority: Optional[Priority] = None,
        worker_timezone: Optional[str] = None,
        scan_limit: int = 1000,
    ) -> Optional[Dict[str, Any]]:
        """Get next due email from queue, optionally scoped by ownership timezone."""
        
        now = time.time()
        ts_iso = datetime.now(timezone.utc).isoformat()
        scan_limit_value = 1000
        try:
            scan_limit_value = max(1, int(scan_limit))
        except (TypeError, ValueError):
            pass
        
        priorities = [priority] if priority else self._weighted_priority_order()
        
        for p in priorities:
            queue_key = self.QUEUE_KEY.format(priority=p.value)
            
            result = self._pop_script(
                keys=[queue_key, self.PROCESSING_KEY],
                args=[now, worker_timezone or "", scan_limit_value],
            )
            
            if result is None:
                continue
            
            email_data = json.loads(result)
            email_data["_processing_started"] = ts_iso
            email_id = email_data.get("_id", "unknown")
            
            # Update processing hash with the enriched data (adds _processing_started)
            self.redis.hset(self.PROCESSING_KEY, email_id, json.dumps(email_data))
            
            logger.info(f"Email popped: {email_id}")
            return email_data
        
        return None
    
    def peek_next_scheduled(self) -> Optional[float]:
        """Return the timestamp of the earliest scheduled (not yet ready) email across all priority queues.
        
        Returns None if all queues are empty.
        """
        earliest = None
        for p in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
            queue_key = self.QUEUE_KEY.format(priority=p.value)
            # Get the item with the lowest score (earliest scheduled)
            results = self.redis.zrange(queue_key, 0, 0, withscores=True)
            if results:
                _, score = results[0]
                if earliest is None or score < earliest:
                    earliest = score
        return earliest
    
    def complete(self, email_id: str, success: bool = True, error: Optional[str] = None):
        """Mark email as completed (success or failure) with per-user tracking"""
        
        # Get from processing
        email_json = self.redis.hget(self.PROCESSING_KEY, email_id)
        if not email_json:
            return
        
        email_data = json.loads(email_json)
        user_id = email_data.get("_user_id")
        
        # Remove from processing
        self.redis.hdel(self.PROCESSING_KEY, email_id)
        
        # Remove from active IDs
        self.redis.srem(self.ACTIVE_IDS_KEY, email_id)
        
        # Update per-user current_queued (decrement since it's done)
        if user_id:
            user_stats_key = self.USER_STATS_KEY.format(user_id=user_id)
            self.redis.hincrby(user_stats_key, "current_queued", -1)
            
            # Ensure it doesn't go negative
            current = int(self.redis.hget(user_stats_key, "current_queued") or 0)
            if current < 0:
                self.redis.hset(user_stats_key, "current_queued", 0)
        
        # Global daily stats
        today = date.today().isoformat()
        daily_key = self.DAILY_STATS_KEY.format(date=today)
        
        if success:
            self.redis.hincrby(self.STATS_KEY, "total_sent", 1)
            self.redis.hincrby(daily_key, "sent", 1)
            self.redis.expire(daily_key, 86400 * 7)  # Keep 7 days
            
            # Update per-user stats
            if user_id:
                user_stats_key = self.USER_STATS_KEY.format(user_id=user_id)
                self.redis.hincrby(user_stats_key, "sent", 1)
                self.redis.hset(user_stats_key, "last_sent_at", datetime.now(timezone.utc).isoformat())
                
                # Daily stats
                user_daily_key = self.USER_DAILY_KEY.format(user_id=user_id, date=today)
                self.redis.hincrby(user_daily_key, "sent", 1)
                self.redis.expire(user_daily_key, 86400 * 7)
            
            logger.info(f"Email completed: {email_id} user={user_id}")
        else:
            # Failed - move to dead letter, NO RETRY (to prevent infinite loops)
            email_data["_last_error"] = error
            email_data["_failed_at"] = datetime.now(timezone.utc).isoformat()
            self._release_email_quota(email_data)
            self.redis.lpush(self.DEAD_LETTER_KEY, json.dumps(email_data))
            self.redis.hincrby(self.STATS_KEY, "total_failed", 1)
            self.redis.hincrby(daily_key, "failed", 1)
            self.redis.expire(daily_key, 86400 * 7)
            
            # Update per-user stats
            if user_id:
                user_stats_key = self.USER_STATS_KEY.format(user_id=user_id)
                self.redis.hincrby(user_stats_key, "failed", 1)
                self.redis.hset(user_stats_key, "last_failed_at", datetime.now(timezone.utc).isoformat())
                
                # Daily stats
                user_daily_key = self.USER_DAILY_KEY.format(user_id=user_id, date=today)
                self.redis.hincrby(user_daily_key, "failed", 1)
                self.redis.expire(user_daily_key, 86400 * 7)
            
            logger.warning(f"Email failed and moved to dead letter: {email_id} user={user_id} - {error}")
    
    def peek(self, priority: Optional[Priority] = None, count: int = 10) -> List[Dict[str, Any]]:
        """Peek at queued emails without removing them"""
        
        results = []
        priorities = [priority] if priority else [Priority.HIGH, Priority.NORMAL, Priority.LOW]
        
        for p in priorities:
            queue_key = self.QUEUE_KEY.format(priority=p.value)
            items = self.redis.zrange(queue_key, 0, count - len(results) - 1)
            
            for item in items:
                results.append(json.loads(item))
                if len(results) >= count:
                    break
            
            if len(results) >= count:
                break
        
        return results
    
    def get_queue_length(self, priority: Optional[Priority] = None) -> Dict[str, int]:
        """Get queue lengths by priority"""
        
        if priority:
            queue_key = self.QUEUE_KEY.format(priority=priority.value)
            return {priority.value: self.redis.zcard(queue_key)}
        
        return {
            "high": self.redis.zcard(self.QUEUE_KEY.format(priority="high")),
            "normal": self.redis.zcard(self.QUEUE_KEY.format(priority="normal")),
            "low": self.redis.zcard(self.QUEUE_KEY.format(priority="low")),
            "processing": self.redis.hlen(self.PROCESSING_KEY),
            "dead_letter": self.redis.llen(self.DEAD_LETTER_KEY),
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics"""
        
        stats = self.redis.hgetall(self.STATS_KEY)
        lengths = self.get_queue_length()
        
        # Current queue = actual items in queue right now
        current_queued = lengths["high"] + lengths["normal"] + lengths["low"]
        
        # Today's global stats
        today = date.today().isoformat()
        daily_key = self.DAILY_STATS_KEY.format(date=today)
        daily_stats = self.redis.hgetall(daily_key)
        today_sent = int(daily_stats.get("sent", 0))
        today_failed = int(daily_stats.get("failed", 0))
        
        return {
            "queued": {
                "high": lengths["high"],
                "normal": lengths["normal"],
                "low": lengths["low"],
                "total": current_queued
            },
            "processing": lengths["processing"],
            "dead_letter": lengths["dead_letter"],
            # Show CURRENT counts prominently
            "current": {
                "queued": current_queued,
                "processing": lengths["processing"],
            },
            # Today's stats (auto-resets daily)
            "today": {
                "sent": today_sent,
                "failed": today_failed,
            },
            "historical": {
                "total_queued": int(stats.get("total_queued", 0)),
                "total_sent": int(stats.get("total_sent", 0)),
                "total_retried": int(stats.get("total_retried", 0)),
                "total_failed": int(stats.get("total_failed", 0)),
            },
            # Top-level totals now show TODAY's stats (not all-time)
            "totals": {
                "queued": current_queued,
                "sent": today_sent,
                "retried": int(stats.get("total_retried", 0)),
                "failed": today_failed,
            }
        }
    
    def get_dead_letters(self, count: int = 50) -> List[Dict[str, Any]]:
        """Get emails from dead letter queue"""
        
        items = self.redis.lrange(self.DEAD_LETTER_KEY, 0, count - 1)
        return [json.loads(item) for item in items]
    
    def retry_dead_letter(self, email_id: str) -> bool:
        """Retry a specific dead letter email"""
        
        items = self.redis.lrange(self.DEAD_LETTER_KEY, 0, -1)
        
        for i, item in enumerate(items):
            email_data = json.loads(item)
            if email_data.get("_id") == email_id:
                # Remove from dead letter
                self.redis.lrem(self.DEAD_LETTER_KEY, 1, item)
                
                # Reset retry count and re-queue
                email_data["_retry_count"] = 0
                self.add(email_data, Priority.LOW)
                
                logger.info(f"Dead letter retried: {email_id}")
                return True
        
        return False
    
    def clear_queue(self, priority: Optional[Priority] = None):
        """Clear queue (use with caution!)"""
        
        if priority:
            queue_key = self.QUEUE_KEY.format(priority=priority.value)
            self.redis.delete(queue_key)
        else:
            for p in Priority:
                queue_key = self.QUEUE_KEY.format(priority=p.value)
                self.redis.delete(queue_key)
        
        logger.warning(f"Queue cleared: {priority.value if priority else 'all'}")

    def drain_campaign(self, campaign_id: int) -> int:
        """Remove all queued (not yet processing) emails for a campaign.

        Scans each priority queue and removes items whose ``_id`` starts with
        ``campaign:<campaign_id>:recipient:``.  Returns the number of items
        removed.  Does NOT touch the processing set (emails already picked up
        by a worker).
        """
        prefix = f"campaign:{campaign_id}:recipient:"
        removed = 0

        for p in Priority:
            queue_key = self.QUEUE_KEY.format(priority=p.value)
            # Fetch ALL items — campaigns are small enough for this
            items = self.redis.zrange(queue_key, 0, -1)
            for item_json in items:
                try:
                    data = json.loads(item_json)
                    eid = data.get("_id", "")
                    if eid.startswith(prefix):
                        if self.redis.zrem(queue_key, item_json):
                            self.redis.srem(self.ACTIVE_IDS_KEY, eid)
                            self._release_email_quota(data)
                            removed += 1
                except (json.JSONDecodeError, TypeError):
                    continue

        logger.info(f"Drained {removed} queued emails for campaign {campaign_id}")
        return removed
    
    def clear_dead_letters(self):
        """Clear dead letter queue"""
        self.redis.delete(self.DEAD_LETTER_KEY)
        logger.warning("Dead letter queue cleared")
    
    def get_user_stats(self, user_id: int) -> Dict[str, Any]:
        """Get queue statistics for a specific user"""
        
        user_stats_key = self.USER_STATS_KEY.format(user_id=user_id)
        stats = self.redis.hgetall(user_stats_key)
        
        # Get today's stats
        today = date.today().isoformat()
        user_daily_key = self.USER_DAILY_KEY.format(user_id=user_id, date=today)
        daily_stats = self.redis.hgetall(user_daily_key)
        
        # Count user's items in queue by scanning (more accurate but slower)
        # For performance, we track current_queued in Redis
        current_queued = int(stats.get("current_queued", 0))
        if current_queued < 0:
            current_queued = 0
        
        return {
            "user_id": user_id,
            "today": {
                "sent": int(daily_stats.get("sent", 0)),
                "failed": int(daily_stats.get("failed", 0)),
                "queued": int(daily_stats.get("queued", 0)),
            },
            "session": {
                "sent": int(stats.get("sent", 0)),
                "failed": int(stats.get("failed", 0)),
                "queued": int(stats.get("queued", 0)),
            },
            "current": {
                "queued": current_queued,
            },
            "last_sent_at": stats.get("last_sent_at"),
            "last_failed_at": stats.get("last_failed_at"),
        }
    
    def get_user_queue_items(self, user_id: int, count: int = 50) -> List[Dict[str, Any]]:
        """Get queued items for a specific user"""
        
        results = []
        
        for p in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
            queue_key = self.QUEUE_KEY.format(priority=p.value)
            items = self.redis.zrange(queue_key, 0, -1)  # Get all items
            
            for item in items:
                email_data = json.loads(item)
                if email_data.get("_user_id") == user_id:
                    results.append(email_data)
                    if len(results) >= count:
                        break
            
            if len(results) >= count:
                break
        
        return results
    
    def get_user_dead_letters(self, user_id: int, count: int = 50) -> List[Dict[str, Any]]:
        """Get dead letter items for a specific user"""
        
        all_items = self.redis.lrange(self.DEAD_LETTER_KEY, 0, -1)
        results = []
        
        for item in all_items:
            email_data = json.loads(item)
            if email_data.get("_user_id") == user_id:
                results.append(email_data)
                if len(results) >= count:
                    break
        
        return results
    
    def reset_user_session_stats(self, user_id: int):
        """Reset session stats for a user (keeps daily stats)"""
        
        user_stats_key = self.USER_STATS_KEY.format(user_id=user_id)
        self.redis.hset(user_stats_key, "sent", 0)
        self.redis.hset(user_stats_key, "failed", 0)
        self.redis.hset(user_stats_key, "queued", 0)
        logger.info(f"Reset session stats for user {user_id}")
    
    def reset_global_stats(self):
        """Reset all global stats (cumulative + daily + dead letter)"""
        
        # Reset cumulative stats
        self.redis.delete(self.STATS_KEY)
        
        # Clear dead letter queue
        self.redis.delete(self.DEAD_LETTER_KEY)
        
        # Clear daily stats keys
        for key in self.redis.scan_iter("email:daily:*"):
            self.redis.delete(key)
        
        # Clear per-user stats
        for key in self.redis.scan_iter("email:user_stats:*"):
            self.redis.delete(key)
        for key in self.redis.scan_iter("email:user_daily:*"):
            self.redis.delete(key)
        
        # Clear active IDs (stale entries)
        self.redis.delete(self.ACTIVE_IDS_KEY)
        
        logger.warning("Global stats reset by admin")
        return True


    # =========================================================================
    # Campaign batching work-queue
    # =========================================================================

    # Atomically move due campaign IDs from the due ZSET into the work LIST.
    _DRAIN_DUE_LUA = """
    local due_key  = KEYS[1]
    local work_key = KEYS[2]
    local now      = tonumber(ARGV[1])
    local limit    = tonumber(ARGV[2]) or 500

    local items = redis.call('ZRANGEBYSCORE', due_key, '-inf', now, 'LIMIT', 0, limit)
    local moved = 0
    for i = 1, #items do
        redis.call('ZREM', due_key, items[i])
        redis.call('RPUSH', work_key, items[i])
        moved = moved + 1
    end
    return moved
    """

    def schedule_campaign(
        self,
        campaign_id: int,
        due_at: Optional[float] = None,
        overwrite: bool = True,
        tz: Optional[str] = None,
    ) -> None:
        """Register a campaign for batching.

        ``due_at`` is an epoch timestamp; defaults to now (batch immediately).
        ``tz`` scopes the schedule to that timezone's workers ("any" when None).
        With ``overwrite=False`` an existing (possibly future) due score is
        preserved, so pacing/cooldown schedules are never disturbed.
        """
        score = due_at if due_at is not None else time.time()
        kwargs = {} if overwrite else {"nx": True}
        self.redis.zadd(
            self.campaign_due_key(tz),
            {str(campaign_id): score},
            **kwargs,
        )
        # Nudge worker threads so they wake when the earliest send is imminent.
        try:
            existing = self.redis.get("queue:wake_at")
            if not existing or score < float(existing):
                self.redis.set("queue:wake_at", str(score), ex=max(int(score - time.time()) + 60, 120))
        except (TypeError, ValueError):
            pass

    def unschedule_campaign(self, campaign_id: int) -> None:
        """Remove a campaign from every timezone's batching schedule."""
        member = str(campaign_id)
        try:
            for key in self.redis.scan_iter(match="campaign:due:*"):
                self.redis.zrem(key, member)
            for key in self.redis.scan_iter(match="campaign:work:*"):
                self.redis.lrem(key, 0, member)
        except Exception:
            pass

    def drain_due_campaigns(self, limit: int = 500, tz: Optional[str] = None) -> int:
        """Move up to ``limit`` due campaign IDs into this timezone's work queue.

        Returns the number of campaigns moved.
        """
        script = self.redis.register_script(self._DRAIN_DUE_LUA)
        try:
            return int(
                script(
                    keys=[self.campaign_due_key(tz), self.campaign_work_key(tz)],
                    args=[time.time(), limit],
                )
                or 0
            )
        except Exception:
            logger.warning("drain_due_campaigns failed", exc_info=True)
            return 0

    def get_campaign_batch_queue_lengths(self, tz: Optional[str] = None) -> Dict[str, int]:
        """Counts for the campaign batching work-queue (due + work)."""
        return {
            "due": self.redis.zcard(self.campaign_due_key(tz)),
            "work": self.redis.llen(self.campaign_work_key(tz)),
        }

    @staticmethod
    def tz_tag(tz_name: Optional[str]) -> str:
        return (tz_name or "any").replace("/", "_")

    @classmethod
    def campaign_due_key(cls, tz_name: Optional[str]) -> str:
        return cls.CAMPAIGN_DUE_KEY.format(tz_tag=cls.tz_tag(tz_name))

    @classmethod
    def campaign_work_key(cls, tz_name: Optional[str]) -> str:
        return cls.CAMPAIGN_WORK_KEY.format(tz_tag=cls.tz_tag(tz_name))

    # =========================================================================
    # Inbox quota reservation (queue-time, timezone-keyed)
    # =========================================================================

    _RESERVE_INBOX_QUOTA_LUA = """
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

    def reserve_inbox_quota(
        self,
        inbox_id: int,
        tz_name: Optional[str],
        tz_date: str,
        n: int = 1,
        cap: int = 0,
    ) -> int:
        """Reserve ``n`` sends against an inbox's daily campaign quota.

        The counter is keyed by the campaign owner's timezone date so the
        daily cap rolls over on the worker's local day boundary. Returns
        ``n`` when reserved, ``0`` when the inbox is exhausted for the day.
        ``cap <= 0`` means unlimited.
        """
        script = self.redis.register_script(self._RESERVE_INBOX_QUOTA_LUA)
        key = self.INBOX_QUOTA_KEY.format(
            inbox_id=inbox_id,
            tz=(tz_name or "unknown").replace("/", "_"),
            date=tz_date,
        )
        try:
            return int(script(keys=[key], args=[cap, n, 172800]) or 0)
        except Exception:
            logger.warning(f"reserve_inbox_quota failed for inbox {inbox_id}", exc_info=True)
            return n  # fail-open

    def release_inbox_quota(
        self,
        inbox_id: int,
        tz_name: Optional[str],
        tz_date: str,
        n: int = 1,
    ) -> None:
        """Release previously reserved inbox quota (e.g. failed/drained sends)."""
        try:
            key = self.INBOX_QUOTA_KEY.format(
                inbox_id=inbox_id,
                tz=(tz_name or "unknown").replace("/", "_"),
                date=tz_date,
            )
            current = int(self.redis.get(key) or 0)
            self.redis.decrby(key, min(n, current))
        except Exception:
            pass

    def _release_email_quota(self, email_data: Dict[str, Any]) -> None:
        """Release domain + inbox quota reservations attached to an email."""
        from services import domain_service as _ds

        domain = email_data.get("_quota_domain")
        if domain:
            _ds.release_daily(domain, day=email_data.get("_quota_domain_date"), n=1)

        inbox_id = email_data.get("_quota_inbox_id")
        if inbox_id is not None:
            self.release_inbox_quota(
                inbox_id,
                email_data.get("_quota_tz"),
                email_data.get("_quota_tz_date") or date.today().isoformat(),
                n=1,
            )


# Singleton instance
email_queue = EmailQueue()
