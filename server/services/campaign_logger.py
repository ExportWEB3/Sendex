"""
Global campaign activity logger.

Provides a single, importable function to push timestamped log entries
to a campaign's Redis activity feed.  Any module — campaign_service,
worker_service, API endpoints — can call ``log_campaign_activity()``
to write a line that appears in the live frontend terminal.
"""

from datetime import datetime, timezone
from services.queue_service import email_queue

# Maximum entries kept per campaign (ring-buffer via LTRIM)
_MAX_ENTRIES = 500
# TTL for the Redis key (24 hours)
_TTL_SECONDS = 86400


def log_campaign_activity(campaign_id: int, message: str) -> None:
    """Push *message* to ``campaign:<id>:activity`` in Redis.

    The entry is prefixed with a UTC timestamp, e.g.
    ``[14:32:07] 📦 Starting micro-batch: 6 emails ...``

    The list is capped at the last :pydata:`_MAX_ENTRIES` entries and
    expires after 24 h of inactivity.
    """
    try:
        r = email_queue.redis
        activity_key = f"campaign:{campaign_id}:activity"
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        entry = f"[{ts}] {message}"
        r.rpush(activity_key, entry)
        r.ltrim(activity_key, -_MAX_ENTRIES, -1)
        r.expire(activity_key, _TTL_SECONDS)
    except Exception:
        # Logging must never crash callers
        pass
