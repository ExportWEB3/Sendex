"""Backfill global Active mode for every eligible warm-up inbox.

Run from the server directory after deploying the day-seven graduation rule:
    python scripts/backfill_active_inbox_modes.py
"""

from datetime import datetime, timezone
from pathlib import Path
import sys


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from database import SessionLocal
from models.inbox import Inbox, InboxState
from models.smtp_account import SMTPAccount
from services.sending_mode_service import determine_mode
from services.warmup_service import WarmupService


def main() -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        warmup_service = WarmupService(db)
        inboxes = db.query(Inbox).filter(Inbox.is_active == True).all()
        changed = []

        for inbox in inboxes:
            if inbox.state == InboxState.WARMING_UP:
                warmup_service.update_warmup_day(inbox)

            smtp = db.query(SMTPAccount).filter(SMTPAccount.id == inbox.smtp_account_id).first()
            provider = (smtp.provider_type if smtp else "smtp") or "smtp"
            state = inbox.state.value if inbox.state else "not_started"
            new_mode, reason = determine_mode(
                provider_type=provider,
                current_daily_count=inbox.current_daily_count or 0,
                daily_cap=inbox.daily_cap or 0,
                inbox_state=state,
                warmup_day=inbox.warmup_day or 0,
                now=now,
            )

            if inbox.sending_mode != new_mode:
                previous = inbox.sending_mode.value if inbox.sending_mode else "none"
                inbox.sending_mode = new_mode
                inbox.last_mode_change = now
                # This is a deliberate policy update, so a stale lock must not
                # leave an eligible inbox on its old profile.
                inbox.mode_locked_until = None
                changed.append((inbox.id, inbox.email, previous, new_mode.value, reason))

        db.commit()
        print(f"Checked {len(inboxes)} active inboxes; updated {len(changed)} mode(s).")
        for inbox_id, email, old, new, reason in changed:
            print(f"- #{inbox_id} {email}: {old} -> {new} ({reason})")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()