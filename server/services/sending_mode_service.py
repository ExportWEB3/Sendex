"""
Sending Mode Engine — Human-Like Behavioural Sending

Two modes that simulate email pacing:
    ACTIVE:     Default pace — every new inbox starts here (day 0).
    HYPER:      Graduated pace — inboxes that last HYPER_GRADUATION_DAYS
                days (default 5) graduate automatically.

The old OFFLINE/DISTRACTED modes are retired: they are never assigned
anymore. Delivery windows are enforced separately by the campaign worker
and must not make the inbox appear offline.
"""

import random
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple

from models.inbox import SendingMode

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# MODE DEFINITIONS — all values are (min, max) tuples, randomized each cycle
# ═══════════════════════════════════════════════════════════════════════════════

MODE_PROFILES: Dict[str, Dict[str, Any]] = {
    SendingMode.ACTIVE: {
        "label": "Active",
        "interval_sec": (60, 240),         # 1–4 minutes between emails
        "batch_size": (4, 10),             # 4–10 emails per micro-batch
        "mini_break_sec": (600, 1500),     # 10–25 minutes
        "macro_break_sec": (1800, 3600),   # 30–60 minutes
        "emails_before_mini": (8, 18),
        "emails_before_macro": (30, 60),
    },
    SendingMode.HYPER: {
        "label": "Hyper",
        "interval_sec": (30, 120),         # 30s–2 minutes between emails
        "batch_size": (6, 14),             # 6–14 emails per micro-batch
        "mini_break_sec": (300, 900),      # 5–15 minutes
        "macro_break_sec": (900, 1800),    # 15–30 minutes
        "emails_before_mini": (12, 25),
        "emails_before_macro": (50, 90),
    },
    # Legacy profiles — kept so stored rows/old payloads still resolve.
    SendingMode.DISTRACTED: {
        "label": "Distracted (legacy)",
        "interval_sec": (300, 720),
        "batch_size": (2, 6),
        "mini_break_sec": (1200, 2700),
        "macro_break_sec": (3600, 7200),
        "emails_before_mini": (5, 12),
        "emails_before_macro": (15, 30),
    },
    SendingMode.OFFLINE: {
        "label": "Offline (legacy)",
        "interval_sec": (900, 2700),
        "batch_size": (1, 3),
        "mini_break_sec": (3600, 10800),
        "macro_break_sec": (10800, 21600),
        "emails_before_mini": (2, 5),
        "emails_before_macro": (6, 12),
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# MODE THRESHOLDS
# ═══════════════════════════════════════════════════════════════════════════════

# Working-hours helpers remain available for delivery scheduling. They do not
# decide an inbox's global sending mode.
WORK_HOUR_START = 9
WORK_HOUR_END = 17

# Inboxes that last this many days graduate ACTIVE → HYPER.
HYPER_GRADUATION_DAYS = int(os.getenv("HYPER_GRADUATION_DAYS", "5"))


# ═══════════════════════════════════════════════════════════════════════════════
# CORE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def get_batch_params(mode: SendingMode) -> Dict[str, Any]:
    """Get randomized sending parameters for the current cycle.
    
    Returns a dict with:
        batch_size: int — how many emails in this micro-batch
        interval_sec: int — seconds between each email in the batch
        mini_break_sec: int — break after batch_size emails
        macro_break_sec: int — long break after many batches
        emails_before_mini: int — emails before triggering a mini break
        emails_before_macro: int — emails before triggering a macro break
    """
    profile = MODE_PROFILES[mode]
    
    return {
        "mode": mode.value,
        "batch_size": random.randint(*profile["batch_size"]),
        "interval_sec": random.randint(*profile["interval_sec"]),
        "mini_break_sec": random.randint(*profile["mini_break_sec"]),
        "macro_break_sec": random.randint(*profile["macro_break_sec"]),
        "emails_before_mini": random.randint(*profile["emails_before_mini"]),
        "emails_before_macro": random.randint(*profile["emails_before_macro"]),
    }


def is_working_hours(now: Optional[datetime] = None, timezone_name: Optional[str] = None) -> bool:
    """Check if current time is within working hours in the selected timezone.

    If timezone_name is omitted, UTC is used for backward compatibility.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    local_now = now
    if timezone_name:
        try:
            from zoneinfo import ZoneInfo

            local_now = now.astimezone(ZoneInfo(timezone_name))
        except Exception:
            # Fall back to UTC if the timezone cannot be resolved.
            local_now = now.astimezone(timezone.utc)

    return WORK_HOUR_START <= local_now.hour < WORK_HOUR_END


def determine_mode(
    provider_type: str,
    current_daily_count: int,
    daily_cap: int,
    inbox_state: str,
    warmup_day: int = 0,
    now: Optional[datetime] = None,
    timezone_name: Optional[str] = None,
) -> Tuple[SendingMode, str]:
    """Determine the optimal sending mode for an inbox.

    Global graduation:
        every inbox starts on ACTIVE (including brand-new ones)
        warmup_day >= HYPER_GRADUATION_DAYS → HYPER

    Args:
        provider_type: SMTP account provider_type (smtp, ses_api, brevo, ses_smtp)
        current_daily_count: Emails sent today
        daily_cap: Daily sending limit
        inbox_state: Inbox warmup state
        warmup_day: Current warmup day
        now: Current datetime (for testing)
        timezone_name: Retained for callers that also use working-hours helpers.

    Returns:
        (mode, reason) tuple
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # ── Graduation: day N → HYPER ──
    if inbox_state == "warmed_up" or warmup_day >= HYPER_GRADUATION_DAYS:
        return (
            SendingMode.HYPER,
            f"Warmup day {warmup_day} >= {HYPER_GRADUATION_DAYS} — graduated to Hyper",
        )

    # ── Everyone else starts on Active (including new inboxes) ──
    return SendingMode.ACTIVE, "Active by default — new inboxes start sending at active pace"


def get_campaign_batch_params(
    mode: SendingMode,
) -> Dict[str, Any]:
    """Get campaign-specific batch parameters based on mode.
    
    This replaces the static 3-7 batch / 15-45s delay / 4-6min cooldown
    with mode-aware values.
    
    Returns:
        micro_batch_size: int
        inter_email_delay_range: (min_sec, max_sec)
        cooldown_range: (min_sec, max_sec)  — pause between batches
    """
    profile = MODE_PROFILES[mode]
    
    batch_size = random.randint(*profile["batch_size"])
    
    # Inter-email delay is a fraction of the interval (emails within a batch
    # are sent faster than the full interval — the interval governs batch-to-batch)
    interval = profile["interval_sec"]
    inter_email_min = max(10, interval[0] // 3)
    inter_email_max = max(20, interval[1] // 3)
    
    # Cooldown between batches = mini_break for normal batches  
    cooldown = profile["mini_break_sec"]
    
    return {
        "mode": mode.value,
        "micro_batch_size": batch_size,
        "inter_email_delay_range": (inter_email_min, inter_email_max),
        "cooldown_range": cooldown,
    }


def get_worker_delay(mode: SendingMode) -> float:
    """Get the human-like delay for the worker loop between individual sends.
    
    This replaces the flat 1-3s delay in _human_delay().
    """
    profile = MODE_PROFILES[mode]
    interval = profile["interval_sec"]
    
    # The worker delay is the interval between emails (the full mode interval)
    # We add some jitter (±20%) to avoid exact patterns
    base = random.randint(*interval)
    jitter = random.uniform(-0.2, 0.2)
    return max(5, base * (1 + jitter))


def should_take_break(
    mode: SendingMode,
    sends_since_last_break: int,
    sends_since_last_macro: int,
) -> Tuple[Optional[str], int]:
    """Check if the worker should take a break.
    
    Returns:
        (break_type, break_duration_sec) — break_type is None, "mini", or "macro"
    """
    profile = MODE_PROFILES[mode]
    
    # Macro break check (takes priority)
    macro_threshold = random.randint(*profile["emails_before_macro"])
    if sends_since_last_macro >= macro_threshold:
        duration = random.randint(*profile["macro_break_sec"])
        return "macro", duration
    
    # Mini break check
    mini_threshold = random.randint(*profile["emails_before_mini"])
    if sends_since_last_break >= mini_threshold:
        duration = random.randint(*profile["mini_break_sec"])
        return "mini", duration
    
    return None, 0


def get_mode_display_info(mode: SendingMode) -> Dict[str, Any]:
    """Get display-friendly info about a mode for the UI."""
    profile = MODE_PROFILES[mode]
    return {
        "mode": mode.value,
        "label": profile["label"],
        "interval": f"{profile['interval_sec'][0] // 60}–{profile['interval_sec'][1] // 60} min",
        "batch_size": f"{profile['batch_size'][0]}–{profile['batch_size'][1]}",
        "mini_break": f"{profile['mini_break_sec'][0] // 60}–{profile['mini_break_sec'][1] // 60} min",
        "macro_break": f"{profile['macro_break_sec'][0] // 60}–{profile['macro_break_sec'][1] // 60} min",
    }
