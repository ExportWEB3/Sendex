"""
Kill Switch Service

Global kill switch that blocks ALL email sending (campaigns + worker).
When enabled, any attempt to start a campaign or worker will be rejected
with "SoF {RESEND} disabled, contact your provider".

Uses Redis key `system:kill_switch` so it works across all processes.

Toggle via:
  POST /api/system/kill-switch/enable
  POST /api/system/kill-switch/disable
  GET  /api/system/kill-switch/status
"""

import os
import logging

from services.redis_client import get_redis_client

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
KILL_SWITCH_KEY = "system:kill_switch"
KILL_SWITCH_MESSAGE = "SoF {RESEND} disabled, contact your provider"


def _get_redis():
    return get_redis_client(REDIS_URL)


def is_kill_switch_enabled() -> bool:
    """Check if the global kill switch is active."""
    try:
        r = _get_redis()
        return r.get(KILL_SWITCH_KEY) == "1"
    except Exception as e:
        # A global shutdown must never silently become permission to send
        # merely because Redis is temporarily unavailable.
        logger.error(f"Kill switch Redis check failed; failing closed: {e}")
        return True


def enable_kill_switch():
    """Enable the kill switch — blocks all sending."""
    r = _get_redis()
    r.set(KILL_SWITCH_KEY, "1")
    logger.warning("KILL SWITCH ENABLED — all sending is now blocked")


def disable_kill_switch():
    """Disable the kill switch — allows sending again."""
    r = _get_redis()
    r.delete(KILL_SWITCH_KEY)
    logger.warning("KILL SWITCH DISABLED — sending is now allowed")
