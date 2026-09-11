"""
Inbox API with multi-tenancy support.
Each user can only access their own inboxes.
Admins can view any user's inboxes via ?user_id=X parameter.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from typing import List, Optional

from database import get_db
from models.inbox import Inbox, InboxState
from models.smtp_account import SMTPAccount
from models.user import User, UserRole
from schemas.inbox import InboxCreate, InboxResponse, InboxUpdate
from api.auth import require_admin, require_auth
from services.imap_auto_detect import detect_imap_from_mx, test_imap_connection, auto_detect_imap
from services.redis_client import get_redis_client
from services.warmup_service import WarmupService
from models.warmup import FallbackReplyEmail
import logging
import os

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/inboxes", tags=["Inboxes"])


TEST_EMAIL_SUBJECT = "Quick delivery check"
TEST_EMAIL_TEXT = (
    "Hi,\n\n"
    "This is a delivery test sent from your configured inbox. "
    "If you received it, sending is working correctly.\n\n"
    "Best,\n"
    "Your email team"
)
TEST_EMAIL_HTML = (
    "<html><body><p>Hi,</p>"
    "<p>This is a delivery test sent from your configured inbox. "
    "If you received it, sending is working correctly.</p>"
    "<p>Best,<br>Your email team</p></body></html>"
)


class InboxTestSendRequest(BaseModel):
    to_email: EmailStr


def get_effective_user_id(current_user: User, requested_user_id: Optional[int] = None) -> int:
    """
    Get the effective user_id for queries.
    - Regular users: always their own ID
    - Admins: can specify another user's ID, otherwise their own
    """
    if requested_user_id is not None and current_user.role == UserRole.ADMIN:
        return requested_user_id
    return current_user.id


@router.get("/", response_model=List[InboxResponse])
def get_all_inboxes(
    user_id: Optional[int] = Query(None, description="Admin only: view another user's inboxes"),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get all inboxes for current user (or specified user for admins)"""
    effective_user_id = get_effective_user_id(current_user, user_id)
    return db.query(Inbox).filter(Inbox.user_id == effective_user_id).all()


@router.get("/fleet-quota")
def get_fleet_quota(
    current_user: User = Depends(require_auth),
):
    """Fleet-wide daily sending budget usage (the one cap that governs campaigns)."""
    from services import domain_service as _ds
    return _ds.get_fleet_usage()


@router.get("/{inbox_id}", response_model=InboxResponse)
def get_inbox(
    inbox_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get a specific inbox by ID"""
    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    
    # Check ownership (admins can view any)
    if inbox.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return inbox


@router.post("/{inbox_id}/send-test")
def send_inbox_test_email(
    inbox_id: int,
    test_data: InboxTestSendRequest,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Send the built-in delivery test from one specific inbox."""
    from datetime import datetime, timezone

    from services.kill_switch import KILL_SWITCH_MESSAGE, is_kill_switch_enabled
    from services.rate_limiter import rate_limiter
    from services.smtp_service import SMTPConnectionError, SMTPSendError, SMTPService

    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    if inbox.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    if not inbox.is_active or inbox.state == InboxState.DISABLED:
        raise HTTPException(status_code=400, detail="This inbox is disabled and cannot send a test email")
    if is_kill_switch_enabled():
        raise HTTPException(status_code=403, detail=KILL_SWITCH_MESSAGE)

    smtp = db.query(SMTPAccount).filter(SMTPAccount.id == inbox.smtp_account_id).first()
    if not smtp or not smtp.is_active:
        raise HTTPException(status_code=400, detail="The inbox's sending account is not active")
    if smtp.user_id != inbox.user_id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="The inbox's sending account is not available to this user")

    daily_cap = inbox.daily_cap or 0
    if daily_cap > 0 and (inbox.current_daily_count or 0) >= daily_cap:
        raise HTTPException(status_code=429, detail=f"Daily inbox limit reached ({daily_cap}/day)")
    try:
        if daily_cap > 0 and not rate_limiter.check_rate_limit(f"inbox:{inbox.id}", daily_cap):
            raise HTTPException(status_code=429, detail=f"Daily inbox limit reached ({daily_cap}/day)")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Could not verify the rate limit for test send from inbox %s: %s", inbox.id, exc)
        raise HTTPException(status_code=503, detail="Test sending is temporarily unavailable")

    try:
        provider = (smtp.provider_type or "smtp").lower()
        details = {}

        # Keep the direct test on the same provider path as normal inbox sends.
        if provider in {"brevo", "ses_api"}:
            from services.resend_service import get_resend_service

            sender = get_resend_service()
            provider_label = "Resend"
            if not sender:
                raise HTTPException(status_code=400, detail="Resend API is not configured")

            result = sender.send_email_sync(
                to_email=str(test_data.to_email),
                to_name=None,
                from_email=smtp.from_email,
                from_name=smtp.from_name or None,
                subject=TEST_EMAIL_SUBJECT,
                html_content=TEST_EMAIL_HTML,
                text_content=TEST_EMAIL_TEXT,
                reply_to=inbox.reply_to_email or None,
            )
            if not result.get("success"):
                raise HTTPException(status_code=400, detail=f"Send error: {result.get('error', 'Provider rejected the test')}")
            details = {"message_id": result.get("message_id")}
        else:
            service = SMTPService(smtp)
            try:
                result = service.send_with_retry(
                    to_email=str(test_data.to_email),
                    subject=TEST_EMAIL_SUBJECT,
                    body_text=TEST_EMAIL_TEXT,
                    body_html=TEST_EMAIL_HTML,
                    reply_to=inbox.reply_to_email or smtp.from_email,
                )
            finally:
                service.disconnect()
            details = {"message_id": result.get("message_id")}
            provider_label = "SMTP"

        now = datetime.now(timezone.utc)
        inbox.current_daily_count = (inbox.current_daily_count or 0) + 1
        inbox.total_sent = (inbox.total_sent or 0) + 1
        inbox.last_sent_at = now
        smtp.last_used_at = now
        smtp.last_error = None
        db.commit()
        rate_limiter.record_send(f"inbox:{inbox.id}")

        logger.info("Inbox delivery test sent from %s to %s", inbox.email, test_data.to_email)
        return {
            "success": True,
            "status": "sent",
            "message": f"Test email sent from {inbox.email} to {test_data.to_email}",
            "provider": provider_label,
            "details": details,
        }
    except HTTPException:
        raise
    except SMTPConnectionError as exc:
        smtp.last_error = str(exc)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Connection error: {exc}")
    except SMTPSendError as exc:
        smtp.last_error = str(exc)
        db.commit()
        raise HTTPException(status_code=400 if exc.is_permanent else 500, detail=f"Send error: {exc.message}")
    except Exception as exc:
        logger.exception("Inbox delivery test failed for %s", inbox.email)
        smtp.last_error = str(exc)
        db.commit()
        raise HTTPException(status_code=500, detail="Could not send the test email")


@router.get("/{inbox_id}/mode")
def get_inbox_mode(
    inbox_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get detailed sending mode info for an inbox — current mode, health breakdown, mode profiles."""
    from models.smtp_account import SMTPAccount as SMTP
    from services.sending_mode_service import (
        determine_mode, get_mode_display_info,
        MODE_PROFILES, SendingMode
    )
    
    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    if inbox.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    smtp_acct = db.query(SMTP).filter(SMTP.id == inbox.smtp_account_id).first()
    provider = getattr(smtp_acct, 'provider_type', 'smtp') or 'smtp'
    
    mode, reason = determine_mode(
        provider_type=provider,
        current_daily_count=inbox.current_daily_count or 0,
        daily_cap=inbox.daily_cap or 0,
        inbox_state=inbox.state.value if inbox.state else "not_started",
        warmup_day=inbox.warmup_day or 0,
    )
    
    return {
        "inbox_id": inbox.id,
        "email": inbox.email,
        "current_mode": inbox.sending_mode.value if inbox.sending_mode else "offline",
        "calculated_mode": mode.value,
        "reason": reason,
        "provider_type": provider,
        "last_mode_change": inbox.last_mode_change.isoformat() if inbox.last_mode_change else None,
        "mode_locked_until": inbox.mode_locked_until.isoformat() if inbox.mode_locked_until else None,
        "metrics": {
            "total_sent": inbox.total_sent or 0,
            "total_replied": inbox.total_replied or 0,
            "bounce_rate": inbox.bounce_rate or 0,
            "reply_rate": inbox.reply_rate or 0,
            "open_rate": inbox.open_rate or 0,
            "spam_rate": inbox.spam_rate or 0,
            "warmup_day": inbox.warmup_day or 0,
            "daily_cap_usage": f"{inbox.current_daily_count or 0}/{inbox.daily_cap or 0}",
        },
        "mode_profiles": {
            m.value: get_mode_display_info(m)
            for m in SendingMode
        },
    }


@router.get("/modes/summary")
def get_modes_summary(
    user_id: Optional[int] = Query(None),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get a summary of all inbox modes for the dashboard."""
    effective_user_id = get_effective_user_id(current_user, user_id)
    inboxes = db.query(Inbox).filter(
        Inbox.user_id == effective_user_id,
        Inbox.is_active == True
    ).all()
    
    summary = {"active": 0, "distracted": 0, "offline": 0, "total": len(inboxes)}
    details = []
    
    for inbox in inboxes:
        mode_val = inbox.sending_mode.value if inbox.sending_mode else "offline"
        summary[mode_val] = summary.get(mode_val, 0) + 1
        details.append({
            "id": inbox.id,
            "email": inbox.email,
            "mode": mode_val,
            "state": inbox.state.value if inbox.state else "not_started",
        })
    
    return {"summary": summary, "inboxes": details}


@router.post("/")
def create_inbox(
    inbox_data: InboxCreate,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Create a new inbox - auto-fills IMAP from linked SMTP account or MX/SMTP detection"""
    data = inbox_data.model_dump()
    imap_source = None  # Track how IMAP was configured
    
    # Check if this user already has an inbox with this email
    existing = db.query(Inbox).filter(
        Inbox.user_id == current_user.id,
        Inbox.email == data.get("email")
    ).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"You already have an inbox with email '{data.get('email')}'"
        )

    smtp = db.query(SMTPAccount).filter(
        SMTPAccount.id == data["smtp_account_id"],
        SMTPAccount.user_id == current_user.id,
    ).first()
    if not smtp:
        raise HTTPException(status_code=404, detail="SMTP account not found")
    
    # Auto-fill IMAP from linked SMTP account if not provided
    has_imap = bool(data.get("imap_host") and data.get("imap_username") and data.get("imap_password"))
    is_resend_account = False
    
    if not has_imap and data.get("smtp_account_id"):
        if smtp and (smtp.provider_type or 'smtp') in ('brevo', 'ses_api'):
            # API-backed accounts use the shared Resend reply mailbox.
            is_resend_account = True

            if not data.get("email"):
                data["email"] = smtp.from_email

            imap_host = os.getenv("RESEND_IMAP_HOST", "") or os.getenv("BREVO_IMAP_HOST", "") or os.getenv("SES_IMAP_HOST", "")
            imap_port = int(os.getenv("RESEND_IMAP_PORT", "") or os.getenv("BREVO_IMAP_PORT", "") or os.getenv("SES_IMAP_PORT", "993"))
            imap_user = os.getenv("RESEND_IMAP_USER", "") or os.getenv("BREVO_IMAP_USER", "") or os.getenv("SES_IMAP_USER", "")
            imap_pass = os.getenv("RESEND_IMAP_PASS", "") or os.getenv("BREVO_IMAP_PASS", "") or os.getenv("SES_IMAP_PASS", "")

            if imap_host and imap_user and imap_pass:
                data["imap_host"] = imap_host
                data["imap_port"] = imap_port
                data["imap_username"] = imap_user
                data["imap_password"] = imap_pass
                data["reply_enabled"] = True
                has_imap = True
                imap_source = "resend_shared_imap"
                logger.info(f"Resend inbox {data['email']} — replies via shared IMAP: {imap_host} ({imap_user})")
            else:
                data["reply_enabled"] = False
                has_imap = False
                imap_source = "none"
                logger.warning(f"Resend inbox {data['email']} — no shared IMAP configuration, replies disabled")
        
        elif smtp:
            email_addr = data.get("email", smtp.username)
            
            # FIRST: Always check MX records to know where replies actually go
            mx_result = None
            mx_imap_works = False
            try:
                mx_result = detect_imap_from_mx(email_addr)
                if mx_result:
                    # Test if SMTP credentials work on the MX-based IMAP
                    test = test_imap_connection(
                        mx_result["imap_host"], mx_result["imap_port"],
                        smtp.username, smtp.password, timeout=10
                    )
                    if test["success"]:
                        mx_imap_works = True
            except Exception as e:
                logger.warning(f"MX detection failed for {email_addr}: {e}")
            
            # Priority 1: If MX detection found a working IMAP, use it
            # (MX tells us where replies actually go - most reliable for reply detection)
            if mx_result and mx_imap_works:
                data["imap_host"] = mx_result["imap_host"]
                data["imap_port"] = mx_result["imap_port"]
                data["imap_username"] = smtp.username
                data["imap_password"] = smtp.password
                has_imap = True
                imap_source = "mx_record"
                logger.info(f"Auto-filled IMAP via MX records: {mx_result['imap_host']} for {email_addr}")
                
                # Also update the SMTP account's saved IMAP to the correct MX-based one
                if smtp.has_imap and smtp.imap_host and mx_result["imap_host"].lower() != smtp.imap_host.lower():
                    logger.info(f"Updating SMTP account IMAP from {smtp.imap_host} to {mx_result['imap_host']} (MX-based)")
                    smtp.imap_host = mx_result["imap_host"]
                    smtp.imap_port = mx_result["imap_port"]
                elif not smtp.has_imap:
                    smtp.imap_host = mx_result["imap_host"]
                    smtp.imap_port = mx_result["imap_port"]
                    smtp.imap_username = smtp.username
                    smtp.imap_password = smtp.password
            
            # Priority 2: Use SMTP account's existing IMAP settings 
            # (only if MX didn't work or MX matches the saved IMAP)
            if not has_imap and smtp.has_imap:
                # Check if SMTP account's IMAP matches MX (if we know MX)
                imap_matches_mx = not mx_result or (smtp.imap_host and mx_result["imap_host"].lower() == smtp.imap_host.lower())
                if imap_matches_mx:
                    data["imap_host"] = smtp.imap_host
                    data["imap_port"] = smtp.imap_port
                    data["imap_username"] = smtp.imap_username
                    data["imap_password"] = smtp.imap_password
                    has_imap = True
                    imap_source = "smtp_account"
                    logger.info(f"Auto-filled IMAP from SMTP account '{smtp.name}': {smtp.imap_host}")
                else:
                    # SMTP account's IMAP doesn't match MX - this means replies go elsewhere
                    logger.warning(
                        f"SMTP account IMAP ({smtp.imap_host}) doesn't match MX ({mx_result['imap_host']}) for {email_addr}. "
                        f"MX IMAP login failed. Replies may not be detected."
                    )
                    # Still use SMTP account's IMAP as fallback, but log the mismatch
                    data["imap_host"] = smtp.imap_host
                    data["imap_port"] = smtp.imap_port
                    data["imap_username"] = smtp.imap_username
                    data["imap_password"] = smtp.imap_password
                    has_imap = True
                    imap_source = "smtp_account"
            
            # Priority 3: Full auto-detect (tries SMTP host as IMAP, common patterns, etc.)
            if not has_imap:
                try:
                    detection = auto_detect_imap(
                        smtp_host=smtp.host,
                        smtp_username=smtp.username,
                        smtp_password=smtp.password
                    )
                    if detection["detected"]:
                        data["imap_host"] = detection["imap_host"]
                        data["imap_port"] = detection["imap_port"]
                        data["imap_username"] = detection["imap_username"]
                        data["imap_password"] = detection["imap_password"]
                        has_imap = True
                        imap_source = detection.get("method", "auto_detect")
                        logger.info(f"Auto-detected IMAP for inbox via {imap_source}: {detection['imap_host']}")
                        
                        # Also save to SMTP account for future inboxes
                        if not smtp.has_imap:
                            smtp.imap_host = detection["imap_host"]
                            smtp.imap_port = detection["imap_port"]
                            smtp.imap_username = detection["imap_username"]
                            smtp.imap_password = detection["imap_password"]
                            logger.info(f"Saved auto-detected IMAP to SMTP account '{smtp.name}'")
                except Exception as e:
                    logger.warning(f"Auto-detect IMAP failed for inbox: {e}")
    
    # Auto-set reply_enabled based on IMAP presence
    if is_resend_account:
        pass  # Already set above based on shared IMAP env vars
    elif data.get("reply_enabled") is None:
        data["reply_enabled"] = has_imap
    elif data["reply_enabled"] and not has_imap:
        raise HTTPException(status_code=400, detail="Cannot enable replies without IMAP configuration")
    
    # If no working IMAP and no reply_to_email, auto-assign global fallback reply-to
    if not has_imap and not data.get("reply_to_email"):
        fallback = db.query(FallbackReplyEmail).filter(
            FallbackReplyEmail.user_id == current_user.id,
            FallbackReplyEmail.is_active == True
        ).first()
        if fallback:
            data["reply_to_email"] = fallback.email
            data["reply_enabled"] = True
            logger.info(f"Auto-assigned global fallback reply-to '{fallback.email}' to new inbox")
    
    inbox = Inbox(**data)
    inbox.user_id = current_user.id  # Assign to current user
    db.add(inbox)
    db.commit()
    db.refresh(inbox)
    
    # Auto-start warmup immediately (no manual Start button needed)
    try:
        warmup_svc = WarmupService(db)
        warmup_result = warmup_svc.start_warmup(inbox)
        logger.info(f"Auto-started warmup for new inbox {inbox.email}: {warmup_result}")
    except Exception as e:
        logger.warning(f"Could not auto-start warmup for {inbox.email}: {e}")
    
    # Build response with detection info
    response = {
        "id": inbox.id,
        "email": inbox.email,
        "smtp_account_id": inbox.smtp_account_id,
        "imap_host": inbox.imap_host,
        "imap_port": inbox.imap_port,
        "group": inbox.group.value if hasattr(inbox.group, 'value') else inbox.group,
        "state": inbox.state.value if hasattr(inbox.state, 'value') else inbox.state,
        "warmup_start_date": inbox.warmup_start_date.isoformat() if inbox.warmup_start_date else None,
        "warmup_day": inbox.warmup_day,
        "daily_cap": inbox.daily_cap,
        "current_daily_count": inbox.current_daily_count,
        "total_sent": inbox.total_sent,
        "total_received": inbox.total_received,
        "total_replied": inbox.total_replied,
        "bounce_rate": inbox.bounce_rate,
        "spam_rate": inbox.spam_rate,
        "open_rate": inbox.open_rate,
        "reply_rate": inbox.reply_rate,
        "is_active": inbox.is_active,
        "reply_enabled": inbox.reply_enabled,
        "reply_to_email": inbox.reply_to_email,
        "has_imap": inbox.has_imap,
        "last_sent_at": inbox.last_sent_at.isoformat() if inbox.last_sent_at else None,
        "last_received_at": inbox.last_received_at.isoformat() if inbox.last_received_at else None,
        "pause_reason": inbox.pause_reason,
        "created_at": inbox.created_at.isoformat() if inbox.created_at else None,
        "updated_at": inbox.updated_at.isoformat() if inbox.updated_at else None,
        # Extra detection info
        "imap_auto_detected": has_imap and imap_source is not None,
        "imap_detection_source": imap_source,
        "is_resend_inbox": is_resend_account,
    }
    return response


@router.put("/{inbox_id}", response_model=InboxResponse)
def update_inbox(
    inbox_id: int,
    inbox_data: InboxUpdate,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Update an existing inbox"""
    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    
    # Check ownership (admins can edit any)
    if inbox.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    update_data = inbox_data.model_dump(exclude_unset=True)

    if "smtp_account_id" in update_data:
        smtp = db.query(SMTPAccount).filter(
            SMTPAccount.id == update_data["smtp_account_id"],
            SMTPAccount.user_id == inbox.user_id,
        ).first()
        if not smtp:
            raise HTTPException(status_code=404, detail="SMTP account not found")
    
    # If IMAP credentials are being removed, auto-disable reply_enabled
    imap_host = update_data.get("imap_host", inbox.imap_host)
    imap_user = update_data.get("imap_username", inbox.imap_username)
    imap_pass = update_data.get("imap_password", inbox.imap_password)
    has_imap = bool(imap_host and imap_user and imap_pass)
    
    if "reply_enabled" in update_data and update_data["reply_enabled"] and not has_imap:
        raise HTTPException(status_code=400, detail="Cannot enable replies without IMAP configuration")
    
    # Auto-disable replies if IMAP is being removed
    if not has_imap and inbox.reply_enabled:
        update_data["reply_enabled"] = False
    
    for field, value in update_data.items():
        setattr(inbox, field, value)
    
    db.commit()
    db.refresh(inbox)
    return inbox


@router.delete("/{inbox_id}")
def delete_inbox(
    inbox_id: int,
    force: bool = False,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Delete an inbox. Use force=true to delete all associated data first."""
    from models.campaign import CampaignRecipient
    from models.warmup import CampaignAutoReply, WarmupPartner, WarmupReply, WarmupThread
    from models.monitoring import DailyMetrics, ReputationHistory, Alert
    from models.email import Email
    
    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    
    # Check ownership (admins can delete any)
    if inbox.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Check for linked data
    linked_count = 0
    linked_count += db.query(CampaignRecipient).filter(CampaignRecipient.inbox_id == inbox_id).count()
    linked_count += db.query(CampaignAutoReply).filter(CampaignAutoReply.inbox_id == inbox_id).count()
    linked_count += db.query(DailyMetrics).filter(DailyMetrics.inbox_id == inbox_id).count()
    
    if linked_count > 0 and not force:
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot delete: inbox has {linked_count} linked records. Use force=true to delete all associated data."
        )
    
    if force:
        # Preserve campaign tracking data - just unlink the inbox
        db.query(CampaignRecipient).filter(CampaignRecipient.inbox_id == inbox_id).update({CampaignRecipient.inbox_id: None})
        db.query(CampaignAutoReply).filter(CampaignAutoReply.inbox_id == inbox_id).update({CampaignAutoReply.inbox_id: None})
        db.query(WarmupPartner).filter(WarmupPartner.inbox_id == inbox_id).delete()
        db.query(WarmupPartner).filter(WarmupPartner.partner_inbox_id == inbox_id).delete()
        db.query(WarmupReply).filter(WarmupReply.inbox_id == inbox_id).delete()
        db.query(WarmupThread).filter(WarmupThread.sender_inbox_id == inbox_id).delete()
        db.query(WarmupThread).filter(WarmupThread.recipient_inbox_id == inbox_id).delete()
        db.query(WarmupThread).filter(WarmupThread.last_sender_id == inbox_id).delete()
        db.query(DailyMetrics).filter(DailyMetrics.inbox_id == inbox_id).delete()
        db.query(ReputationHistory).filter(ReputationHistory.inbox_id == inbox_id).delete()
        db.query(Alert).filter(Alert.inbox_id == inbox_id).delete()
        db.query(Email).filter(Email.inbox_id == inbox_id).delete()
    
    db.delete(inbox)
    db.commit()
    return {"success": True, "message": "Inbox deleted successfully"}


@router.post("/{inbox_id}/check-mx")
def check_inbox_mx(
    inbox_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Check if inbox IMAP matches MX records (for reply detection)"""
    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    if inbox.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=404, detail="Inbox not found")
    
    # Check MX for the inbox email domain
    mx_result = detect_imap_from_mx(inbox.email)
    
    result = {
        "inbox_id": inbox_id,
        "email": inbox.email,
        "current_imap_host": inbox.imap_host,
        "mx_imap_host": mx_result["imap_host"] if mx_result else None,
        "mx_imap_port": mx_result["imap_port"] if mx_result else None,
        "mismatch": False,
        "message": ""
    }
    
    if not mx_result:
        result["message"] = "Could not determine IMAP from MX records"
        return result
    
    if inbox.imap_host and inbox.imap_host.lower() != mx_result["imap_host"].lower():
        result["mismatch"] = True
        result["message"] = (
            f"MX records route replies to {mx_result['imap_host']}, "
            f"but IMAP is set to {inbox.imap_host}. Replies may not be detected!"
        )
    elif not inbox.imap_host:
        result["mismatch"] = True
        result["message"] = f"No IMAP configured. Replies go to {mx_result['imap_host']}."
    else:
        result["message"] = "IMAP matches MX records - replies will be detected correctly."
    
    return result


@router.post("/{inbox_id}/fix-imap")
def fix_inbox_imap(
    inbox_id: int,
    imap_password: Optional[str] = None,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Fix IMAP settings based on MX records. Optionally provide a different IMAP password."""
    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    
    if inbox.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Detect correct IMAP from MX
    mx_result = detect_imap_from_mx(inbox.email)
    if not mx_result:
        raise HTTPException(status_code=400, detail="Could not detect IMAP from MX records for this domain")
    
    # Get SMTP account for credentials
    smtp = db.query(SMTPAccount).filter(
        SMTPAccount.id == inbox.smtp_account_id,
        SMTPAccount.user_id == inbox.user_id,
    ).first()
    password = imap_password or (inbox.imap_password if inbox.imap_password else (smtp.password if smtp else None))
    username = inbox.imap_username or (smtp.username if smtp else inbox.email)
    
    if not password:
        raise HTTPException(status_code=400, detail="No password available. Please provide imap_password.")
    
    # Test the connection
    test = test_imap_connection(mx_result["imap_host"], mx_result["imap_port"], username, password, timeout=10)
    
    if test["success"]:
        inbox.imap_host = mx_result["imap_host"]
        inbox.imap_port = mx_result["imap_port"]
        inbox.imap_username = username
        inbox.imap_password = password
        inbox.reply_enabled = True
        
        # Also update the SMTP account's saved IMAP to match MX
        if smtp and smtp.imap_host and smtp.imap_host.lower() != mx_result["imap_host"].lower():
            smtp.imap_host = mx_result["imap_host"]
            smtp.imap_port = mx_result["imap_port"]
            smtp.imap_username = username
            smtp.imap_password = password
            logger.info(f"Also updated SMTP account '{smtp.name}' IMAP to {mx_result['imap_host']}")
        
        db.commit()
        db.refresh(inbox)
        return {
            "success": True,
            "message": f"IMAP updated to {mx_result['imap_host']}:{mx_result['imap_port']} - replies will now be detected!",
            "imap_host": mx_result["imap_host"],
            "imap_port": mx_result["imap_port"],
            "tested": True
        }
    else:
        return {
            "success": False,
            "message": f"MX points to {mx_result['imap_host']} but login failed: {test['message']}. "
                       f"You may need a different password for this mail provider.",
            "imap_host": mx_result["imap_host"],
            "imap_port": mx_result["imap_port"],
            "tested": True
        }


class SetupReplyToRequest(BaseModel):
    reply_to_email: EmailStr
    reply_to_password: str


@router.post("/{inbox_id}/setup-reply-to")
def setup_reply_to(
    inbox_id: int,
    data: SetupReplyToRequest,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """
    Set up a Reply-To address with auto IMAP detection.
    1. Detects IMAP settings for the reply-to email (via MX + provider map)
    2. Tests the connection
    3. Creates/updates an inbox for the reply-to address with IMAP
    4. Sets reply_to_email on the original inbox
    """
    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    
    if inbox.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    reply_email = data.reply_to_email.lower().strip()
    reply_password = data.reply_to_password
    
    # Step 1: Detect IMAP for the reply-to email
    detection = auto_detect_imap(
        smtp_host="",  # We don't know the SMTP host
        smtp_username=reply_email,
        smtp_password=reply_password
    )
    
    # Also try MX-based detection
    mx_result = detect_imap_from_mx(reply_email)
    
    imap_host = None
    imap_port = 993
    
    if detection.get("detected") and detection.get("method") not in ("failed", "send_only"):
        imap_host = detection["imap_host"]
        imap_port = detection["imap_port"]
    elif mx_result:
        imap_host = mx_result["imap_host"]
        imap_port = mx_result["imap_port"]
    
    if not imap_host:
        # Fallback: try common patterns
        domain = reply_email.split("@")[-1]
        for candidate_host in [f"imap.{domain}", f"mail.{domain}", domain]:
            test = test_imap_connection(candidate_host, 993, reply_email, reply_password, timeout=8)
            if test["success"]:
                imap_host = candidate_host
                imap_port = 993
                break
    
    if not imap_host:
        return {
            "success": False,
            "message": f"Could not detect IMAP settings for {reply_email}. Check the email and password.",
            "step": "detect"
        }
    
    # Step 2: Test the IMAP connection
    test = test_imap_connection(imap_host, imap_port, reply_email, reply_password, timeout=10)
    if not test["success"]:
        return {
            "success": False,
            "message": f"IMAP detected ({imap_host}) but login failed: {test['message']}. "
                       f"For Gmail, use an App Password (Google Account → Security → App Passwords).",
            "imap_host": imap_host,
            "step": "auth"
        }
    
    # Step 3: Create or update the reply-to inbox
    reply_inbox = db.query(Inbox).filter(
        Inbox.email == reply_email,
        Inbox.user_id == inbox.user_id,
    ).first()
    if reply_inbox:
        # Update existing inbox with IMAP
        reply_inbox.imap_host = imap_host
        reply_inbox.imap_port = imap_port
        reply_inbox.imap_username = reply_email
        reply_inbox.imap_password = reply_password
        reply_inbox.reply_enabled = True
        logger.info(f"Updated reply-to inbox {reply_email} with IMAP {imap_host}")
    else:
        # Create new inbox - use the same SMTP account as the original (for sending auto-replies FROM the reply-to)
        reply_inbox = Inbox(
            email=reply_email,
            smtp_account_id=inbox.smtp_account_id,
            imap_host=imap_host,
            imap_port=imap_port,
            imap_username=reply_email,
            imap_password=reply_password,
            reply_enabled=True,
            user_id=inbox.user_id,
            daily_cap=500,
        )
        db.add(reply_inbox)
        logger.info(f"Created reply-to inbox {reply_email} with IMAP {imap_host}")
    
    # Step 4: Set reply_to_email on the original inbox
    inbox.reply_to_email = reply_email
    
    db.commit()
    db.refresh(inbox)
    
    return {
        "success": True,
        "message": f"Reply-To set up! Emails will show as from {inbox.email}, replies go to {reply_email} (checked via {imap_host})",
        "reply_to_email": reply_email,
        "imap_host": imap_host,
        "reply_inbox_id": reply_inbox.id if reply_inbox else None,
        "step": "done"
    }


@router.post("/{inbox_id}/diagnose-replies")
def diagnose_inbox_replies(
    inbox_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """
    Deep diagnostic: scan IMAP inbox and show exactly what's there.
    Shows all emails, which are replies, from whom, and why they don't match campaigns.
    """
    from services.imap_service import IMAPService
    from models.campaign import Campaign, CampaignStatus, CampaignRecipient, RecipientSendStatus
    from models.recipient import Recipient
    from datetime import timedelta, timezone
    import dns.resolver
    
    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    if inbox.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=404, detail="Inbox not found")
    
    result = {
        "inbox_id": inbox.id,
        "inbox_email": inbox.email,
        "imap_host": inbox.imap_host,
        "imap_configured": inbox.has_imap,
        "reply_enabled": inbox.reply_enabled,
        "reply_to_email": inbox.reply_to_email,
        "mx_info": None,
        "imap_connection": None,
        "emails_in_inbox": 0,
        "reply_emails": 0,
        "bounce_emails": 0,
        "read_receipt_emails": 0,
        "matched_to_campaigns": 0,
        "issues": [],
        "email_samples": [],
        "campaigns_using_inbox": [],
        "all_campaign_recipients": [],
    }
    
    # Step 1: Check MX records vs IMAP server
    try:
        domain = inbox.email.split("@")[1]
        mx_records = dns.resolver.resolve(domain, "MX")
        mx_hosts = [str(mx.exchange).rstrip(".") for mx in mx_records]
        result["mx_info"] = {
            "domain": domain,
            "mx_records": mx_hosts,
            "imap_host": inbox.imap_host,
            "match": any(inbox.imap_host in mx for mx in mx_hosts),
        }
        if not result["mx_info"]["match"]:
            result["issues"].append(
                f"MX MISMATCH: Emails to {inbox.email} are routed to {mx_hosts[0]} (MX), "
                f"but IMAP checks {inbox.imap_host}. Replies will go to MX server, not IMAP server! "
                f"Solution: Set up a Reply-To address on a provider that matches (e.g., Gmail)."
            )
    except Exception as e:
        result["mx_info"] = {"error": str(e)}
    
    # Step 2: Check IMAP connection
    if not inbox.has_imap:
        result["imap_connection"] = {"error": "IMAP not configured"}
        result["issues"].append("IMAP credentials not configured - cannot check for replies")
        return result
    
    try:
        imap = IMAPService(inbox.imap_host, inbox.imap_port, inbox.imap_username, inbox.imap_password, True)
        if not imap.connect():
            result["imap_connection"] = {"error": "Connection failed", "host": inbox.imap_host}
            result["issues"].append(f"Cannot connect to IMAP at {inbox.imap_host}:{inbox.imap_port}")
            return result
        
        result["imap_connection"] = {"status": "connected", "host": inbox.imap_host}
        
        # Fetch emails from last 72 hours
        from datetime import datetime
        emails = imap.fetch_recent_emails(folder="INBOX", since_hours=72, limit=200)
        result["emails_in_inbox"] = len(emails)
        
        reply_emails = [m for m in emails if m.get("is_reply")]
        bounce_emails = [m for m in emails if m.get("is_bounce")]
        read_receipt_emails = [m for m in emails if m.get("is_read_receipt")]
        result["reply_emails"] = len(reply_emails)
        result["bounce_emails"] = len(bounce_emails)
        result["read_receipt_emails"] = len(read_receipt_emails)
        
        # Show sample emails with classification
        for m in emails[:30]:
            email_type = "bounce" if m.get("is_bounce") else ("read_receipt" if m.get("is_read_receipt") else ("reply" if m.get("is_reply") else "other"))
            result["email_samples"].append({
                "from": m.get("from_email", ""),
                "to": m.get("to_email", ""),
                "subject": m.get("subject", "")[:80],
                "date": m.get("date", ""),
                "type": email_type,
                "is_reply": m.get("is_reply", False),
                "is_bounce": m.get("is_bounce", False),
                "is_read_receipt": m.get("is_read_receipt", False),
                "in_reply_to": bool(m.get("in_reply_to")),
            })
        
        imap.disconnect()
    except Exception as e:
        result["imap_connection"] = {"error": str(e)}
        result["issues"].append(f"IMAP error: {e}")
        return result
    
    # Step 3: Check which campaigns use this inbox
    from datetime import datetime
    recent_date = datetime.now(timezone.utc) - timedelta(days=30)
    campaigns = db.query(Campaign).filter(
        Campaign.user_id == inbox.user_id,
        Campaign.status.in_([CampaignStatus.RUNNING, CampaignStatus.COMPLETED, CampaignStatus.PAUSED]),
        Campaign.started_at >= recent_date
    ).all()
    
    all_recipients = set()
    for campaign in campaigns:
        if inbox.id in (campaign.inbox_ids or []):
            # Get recipients
            crs = db.query(CampaignRecipient, Recipient).join(
                Recipient, CampaignRecipient.recipient_id == Recipient.id
            ).filter(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.status == RecipientSendStatus.SENT
            ).all()
            
            recipients = [r.email.lower() for _, r in crs]
            all_recipients.update(recipients)
            
            result["campaigns_using_inbox"].append({
                "id": campaign.id,
                "name": campaign.name,
                "status": campaign.status.value,
                "sent_recipients": recipients,
            })
    
    result["all_campaign_recipients"] = list(all_recipients)
    
    # Step 4: Check if any reply emails match campaign recipients
    for m in reply_emails:
        from_email = m.get("from_email", "").lower()
        if from_email in all_recipients:
            result["matched_to_campaigns"] += 1
    
    if reply_emails and not result["matched_to_campaigns"]:
        reply_senders = list(set(m.get("from_email", "").lower() for m in reply_emails))[:10]
        result["issues"].append(
            f"Found {len(reply_emails)} reply emails in inbox, but NONE from campaign recipients. "
            f"Reply senders: {reply_senders[:5]}. "
            f"Campaign recipients: {list(all_recipients)[:10]}. "
            f"This means campaign recipients' replies are going elsewhere (likely MX server)."
        )
    
    if bounce_emails:
        result["issues"].append(
            f"Found {len(bounce_emails)} bounce/NDR emails (from postmaster/mailer-daemon). "
            f"These indicate delivery failures, not real replies."
        )
    
    if read_receipt_emails:
        result["issues"].append(
            f"Found {len(read_receipt_emails)} read receipt emails. "
            f"These are automatic notifications, not real replies."
        )
    
    if not reply_emails and len(emails) > 0:
        result["issues"].append(
            f"Found {len(emails)} emails but none are replies. No In-Reply-To headers or Re: subjects detected."
        )
    
    if not campaigns:
        result["issues"].append("No recent campaigns found (last 30 days) to check replies for.")
    
    if not result["issues"]:
        result["issues"].append("Everything looks correctly configured. If replies aren't being detected, the recipients may not have replied yet.")
    
    return result


@router.get("/diagnostic/last-check")
def get_last_imap_diagnostic(
    current_user: User = Depends(require_admin),
):
    """Get the diagnostic results from the last automated IMAP check."""
    import json as _json
    
    try:
        r = get_redis_client()
        data = r.get("imap_check:last_diagnostic")
        if data:
            return _json.loads(data)
        return {"message": "No diagnostic data yet. Wait for the next IMAP check cycle (every 5 min)."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Redis error: {e}")
