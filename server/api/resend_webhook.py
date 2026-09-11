"""
Resend Webhook API

Handles Resend delivery events:
- email.delivered  → confirm delivery
- email.opened     → record open (Resend tracks this server-side, no pixel needed)
- email.clicked    → record click
- email.bounced    → record bounce, update inbox metrics
- email.complained → record spam complaint

Configuration:
  Set RESEND_WEBHOOK_SECRET in .env (from Resend dashboard → Webhooks).
  Add webhook URL: https://yourdomain.com/api/resend/webhook

This endpoint is PUBLIC (no auth) because Resend needs to call it.
Security is via the webhook signing secret (svix header validation).
"""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import json
import logging
import os
import resend

from database import SessionLocal
from models.inbox import Inbox
from models.smtp_account import SMTPAccount

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/resend", tags=["Resend Webhooks"])

RESEND_WEBHOOK_SECRET = os.getenv("RESEND_WEBHOOK_SECRET", "")


def _verify_webhook(body: bytes, request: Request) -> None:
    """Verify the Svix signature attached to every Resend webhook."""
    if not RESEND_WEBHOOK_SECRET:
        logger.error("RESEND_WEBHOOK_SECRET is not configured")
        raise HTTPException(503, "Webhook verification is not configured")

    try:
        resend.Webhooks.verify({
            "payload": body.decode("utf-8"),
            "headers": {
                "id": request.headers.get("svix-id", ""),
                "timestamp": request.headers.get("svix-timestamp", ""),
                "signature": request.headers.get("svix-signature", ""),
            },
            "webhook_secret": RESEND_WEBHOOK_SECRET,
        })
    except (UnicodeDecodeError, ValueError) as exc:
        logger.warning("Rejected invalid Resend webhook signature: %s", exc)
        raise HTTPException(400, "Invalid webhook signature")


def _find_inbox_by_from_email(db: Session, from_email: str):
    """Find the inbox associated with a from_email address."""
    if not from_email:
        return None

    from_lower = from_email.lower()

    # Direct match on inbox email
    inbox = db.query(Inbox).filter(
        Inbox.email == from_lower,
        Inbox.is_active == True,
    ).first()
    if inbox:
        return inbox

    # Match via SMTP account from_email
    smtp_acct = db.query(SMTPAccount).filter(
        SMTPAccount.from_email == from_lower,
        SMTPAccount.is_active == True,
    ).first()
    if smtp_acct:
        inbox = db.query(Inbox).filter(
            Inbox.smtp_account_id == smtp_acct.id,
            Inbox.is_active == True,
        ).first()
        if inbox:
            return inbox

    return None


@router.post("/webhook")
async def resend_webhook(request: Request):
    """
    Receive Resend webhook events.
    
    Resend sends JSON payloads with event types:
      email.sent, email.delivered, email.opened,
      email.clicked, email.bounced, email.complained,
      email.delivery_delayed
    """
    body = await request.body()
    _verify_webhook(body, request)

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    event_type = payload.get("type", "")
    data = payload.get("data", {})

    logger.info(f"Resend webhook: {event_type} | to={data.get('to', [])} | from={data.get('from', '')}")

    db = SessionLocal()
    try:
        # Extract the "from" email (strip name portion if present)
        from_raw = data.get("from", "")
        # "Name <email>" → extract email
        if "<" in from_raw and ">" in from_raw:
            from_email = from_raw.split("<")[1].split(">")[0].strip().lower()
        else:
            from_email = from_raw.strip().lower()

        inbox = _find_inbox_by_from_email(db, from_email)

        if event_type == "email.delivered":
            _handle_delivered(db, inbox, data)

        elif event_type == "email.opened":
            _handle_opened(db, inbox, data)

        elif event_type == "email.clicked":
            _handle_clicked(db, inbox, data)

        elif event_type == "email.bounced":
            _handle_bounced(db, inbox, data)

        elif event_type == "email.complained":
            _handle_complained(db, inbox, data)

        else:
            logger.debug(f"Resend webhook ignored event: {event_type}")

        db.commit()
    except Exception as e:
        logger.error(f"Error processing Resend webhook: {e}")
        db.rollback()
    finally:
        db.close()

    return {"status": "ok"}


def _handle_delivered(db: Session, inbox, data: dict):
    """Record successful delivery."""
    email_id = data.get("email_id", "")
    if email_id:
        try:
            from models.campaign import Campaign, CampaignRecipient

            message_ids = [email_id, f"<{email_id}@resend.dev>"]
            campaign_recipient = db.query(CampaignRecipient).filter(
                CampaignRecipient.sent_message_id.in_(message_ids)
            ).first()
            if campaign_recipient and campaign_recipient.delivered_at is None:
                campaign_recipient.delivered_at = datetime.now(timezone.utc)
                campaign = db.query(Campaign).filter(
                    Campaign.id == campaign_recipient.campaign_id
                ).first()
                if campaign:
                    campaign.total_delivered = (campaign.total_delivered or 0) + 1
        except Exception as exc:
            logger.debug("Campaign delivery update failed: %s", exc)

    if not inbox:
        logger.info("Resend delivered: %s (no matching inbox)", data.get("to", []))
        return

    # Record in DailyMetrics
    try:
        from services.monitoring_service import MetricsService
        svc = MetricsService(db)
        svc.record_delivery(inbox.id, delivered=True)
    except Exception as e:
        logger.debug(f"Metrics record_delivery failed: {e}")

    logger.info(f"Resend delivered: {data.get('to', [])} via {inbox.email}")


def _handle_opened(db: Session, inbox, data: dict):
    """Record email open (Resend tracks opens server-side, no pixel needed)."""
    if not inbox:
        return

    # Record in DailyMetrics
    try:
        from services.monitoring_service import MetricsService
        svc = MetricsService(db)
        svc.record_engagement(inbox.id, "open")
    except Exception as e:
        logger.debug(f"Metrics record open failed: {e}")

    # Also try to mark the campaign recipient as opened
    _mark_recipient_opened(db, data)

    logger.info(f"Resend open: {data.get('to', [])} via {inbox.email}")


def _handle_clicked(db: Session, inbox, data: dict):
    """Record link click."""
    if not inbox:
        return

    try:
        from services.monitoring_service import MetricsService
        svc = MetricsService(db)
        svc.record_engagement(inbox.id, "click")
    except Exception as e:
        logger.debug(f"Metrics record click failed: {e}")

    logger.info(f"Resend click: {data.get('to', [])} via {inbox.email}")


def _handle_bounced(db: Session, inbox, data: dict):
    """Record bounce and update inbox metrics."""
    to_emails = data.get("to", [])
    bounce_type = data.get("bounce", {}).get("type", "unknown") if isinstance(data.get("bounce"), dict) else "unknown"

    # Record bounce in DailyMetrics
    if inbox:
        try:
            from services.monitoring_service import MetricsService
            svc = MetricsService(db)
            bounce_cat = "hard" if bounce_type in ("hard", "permanent") else "soft"
            svc.record_delivery(inbox.id, delivered=False, bounce_type=bounce_cat)
        except Exception as e:
            logger.debug(f"Metrics record bounce failed: {e}")

    # Feed the domain circuit breaker
    try:
        from services import domain_service as _ds
        domain = _ds.extract_domain(inbox.email if inbox else (data.get("from") or ""))
        _ds.record_bounce(
            domain,
            event_id=data.get("email_id") or None,
        )
        if domain:
            _ds.evaluate_domain_health(domain)
    except Exception as e:
        logger.debug(f"Domain bounce record failed: {e}")

    # Mark campaign recipients as bounced
    for to_email in to_emails:
        _mark_recipient_bounced(db, to_email.lower(), bounce_type)

    logger.warning(f"Resend bounce ({bounce_type}): {to_emails} via {inbox.email if inbox else '?'}")


def _handle_complained(db: Session, inbox, data: dict):
    """Record spam complaint — serious event."""
    to_emails = data.get("to", [])

    if inbox:
        try:
            from services.monitoring_service import MetricsService
            svc = MetricsService(db)
            svc.record_spam_complaint(inbox.id)
        except Exception as e:
            logger.debug(f"Metrics record complaint failed: {e}")

    # Feed the domain circuit breaker
    try:
        from services import domain_service as _ds
        domain = _ds.extract_domain(inbox.email if inbox else (data.get("from") or ""))
        _ds.record_complaint(domain)
        if domain:
            _ds.evaluate_domain_health(domain)
    except Exception as e:
        logger.debug(f"Domain complaint record failed: {e}")

    # Unsubscribe the complaining recipients
    for to_email in to_emails:
        _unsubscribe_recipient(db, to_email.lower())

    logger.warning(f"Resend spam complaint: {to_emails} via {inbox.email if inbox else '?'}")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _mark_recipient_opened(db: Session, data: dict):
    """Try to mark the campaign recipient as opened based on resend email id or to address."""
    try:
        to_emails = data.get("to", [])
        if not to_emails:
            return

        from models.campaign import CampaignRecipient
        from models.recipient import Recipient

        for to_email in to_emails:
            cr = db.query(CampaignRecipient).join(
                Recipient, CampaignRecipient.recipient_id == Recipient.id
            ).filter(
                Recipient.email == to_email.lower(),
                CampaignRecipient.status == "sent",
                CampaignRecipient.opened_at == None,
            ).order_by(CampaignRecipient.sent_at.desc()).first()

            if cr:
                cr.opened_at = datetime.now(timezone.utc)
    except Exception as e:
        logger.debug(f"_mark_recipient_opened error: {e}")


def _mark_recipient_bounced(db: Session, to_email: str, bounce_type: str):
    """Mark campaign recipient as bounced."""
    try:
        from models.campaign import CampaignRecipient
        from models.recipient import Recipient

        cr = db.query(CampaignRecipient).join(
            Recipient, CampaignRecipient.recipient_id == Recipient.id
        ).filter(
            Recipient.email == to_email,
            CampaignRecipient.status == "sent",
        ).order_by(CampaignRecipient.sent_at.desc()).first()

        if cr:
            cr.status = "bounced"
            cr.error_message = f"Resend bounce: {bounce_type}"
    except Exception as e:
        logger.debug(f"_mark_recipient_bounced error: {e}")


def _unsubscribe_recipient(db: Session, email: str):
    """Unsubscribe a recipient who filed a spam complaint."""
    try:
        from models.recipient import Recipient

        r = db.query(Recipient).filter(Recipient.email == email).first()
        if r:
            r.is_active = False
            r.unsubscribed = True
            logger.info(f"Unsubscribed {email} due to spam complaint")
    except Exception as e:
        logger.debug(f"_unsubscribe_recipient error: {e}")
