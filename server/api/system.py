"""
System API Endpoints

Provides REST API for:
- System reset (emergency recovery)
- System resync (reconcile DB with Redis)
- Health checks
"""

from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
import os

from database import get_db
from models.campaign import Campaign, CampaignRecipient, CampaignStatus, RecipientSendStatus
from models.warmup import CampaignAutoReply, FallbackReplyEmail
from models.inbox import Inbox
from models.recipient import Recipient
from models.user import User
from services.queue_service import email_queue, Priority
from services.template_engine import TemplateEngine
from services.kill_switch import (
    is_kill_switch_enabled, enable_kill_switch, disable_kill_switch, KILL_SWITCH_MESSAGE,
)
from services.redis_client import get_redis_client
from api.auth import require_auth, require_admin

router = APIRouter(prefix="/api/system", tags=["System"])

@router.get("/workers/status")
def get_worker_status(current_user: User = Depends(require_auth)):
    """
    Get status of all timezone workers.
    Returns status for each configured worker (US/Eastern, US/Pacific).
    Workers write their status to Redis every cycle.
    """
    import json as _json
    r = get_redis_client()

    # Instance list: name → timezone. Extra instances (e.g. us_eastern_2) share
    # a timezone and are configured via WORKER_INSTANCES env (name:tz pairs).
    default_instances = "us_eastern:US/Eastern,us_eastern_2:US/Eastern,us_eastern_3:US/Eastern,us_pacific:US/Pacific,us_pacific_2:US/Pacific,us_pacific_3:US/Pacific"
    instances_raw = (os.getenv("WORKER_INSTANCES") or default_instances).strip()
    instances = []
    for part in instances_raw.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, tz = part.partition(":")
        instances.append((name.strip(), (tz or "US/Eastern").strip()))

    workers = []
    for name, tz in instances:
        key = f"worker:{name.replace('/', '_').lower()}:status"
        raw = r.get(key)
        if raw:
            try:
                info = _json.loads(raw)
                info["alive"] = True
                workers.append(info)
            except Exception:
                workers.append({"worker_name": name, "timezone": tz, "alive": False, "status": "unknown"})
        else:
            workers.append({"worker_name": name, "timezone": tz, "alive": False, "status": "offline"})
    
    return {"workers": workers, "server_time": datetime.now(timezone.utc).isoformat()}


class FallbackEmailConfig(BaseModel):
    email: str
    app_password: str
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    imap_host: Optional[str] = None
    imap_port: int = 993


class ResetOptions(BaseModel):
    """Options for system reset"""
    clear_queue: bool = True
    reset_pending_recipients: bool = True
    reset_auto_replies: bool = True
    restart_worker: bool = True


class ResyncResult(BaseModel):
    """Result of resync operation"""
    campaigns_checked: int = 0
    recipients_requeued: int = 0
    orphaned_processing_cleared: int = 0
    auto_replies_requeued: int = 0
    errors: list = []


@router.post("/reset")
def system_reset(
    options: ResetOptions = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Emergency system reset.
    
    This will:
    1. Stop the worker
    2. Clear Redis queues
    3. Reset stuck campaign recipients
    4. Restart worker
    """
    if options is None:
        options = ResetOptions()
    
    results = {
        "success": True,
        "actions_taken": [],
        "errors": []
    }
    
    try:
        # Connect to Redis directly
        redis_client = get_redis_client()
        
        # 1. Clear queues if requested
        if options.clear_queue:
            try:
                # Clear all email queues
                redis_client.delete("email:queue:high")
                redis_client.delete("email:queue:normal")
                redis_client.delete("email:queue:low")
                redis_client.delete("email:processing")
                redis_client.delete("email:dead_letter")
                redis_client.delete("email:stats")
                results["actions_taken"].append("Cleared all Redis queues")
            except Exception as e:
                results["errors"].append(f"Failed to clear queues: {str(e)}")
        
        # 2. Reset pending recipients if requested
        if options.reset_pending_recipients:
            try:
                # Find all PENDING recipients with queued_at set (stuck in limbo)
                updated = db.query(CampaignRecipient).filter(
                    CampaignRecipient.status == RecipientSendStatus.PENDING,
                    CampaignRecipient.queued_at.isnot(None)
                ).update({"queued_at": None}, synchronize_session=False)
                
                db.commit()
                results["actions_taken"].append(f"Reset {updated} stuck recipients")
            except Exception as e:
                db.rollback()
                results["errors"].append(f"Failed to reset recipients: {str(e)}")
        
        # 3. Reset auto-replies if requested
        if options.reset_auto_replies:
            try:
                # Reset pending auto-replies that were queued but not sent
                updated = db.query(CampaignAutoReply).filter(
                    CampaignAutoReply.status == "queued"
                ).update({"status": "pending"}, synchronize_session=False)
                
                db.commit()
                results["actions_taken"].append(f"Reset {updated} auto-replies to pending")
            except Exception as e:
                db.rollback()
                results["errors"].append(f"Failed to reset auto-replies: {str(e)}")
        
        if results["errors"]:
            results["success"] = False
        
        return results
        
    except Exception as e:
        return {
            "success": False,
            "actions_taken": results.get("actions_taken", []),
            "errors": [str(e)]
        }


@router.post("/resync")
def system_resync(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Resync database state with Redis queue.
    
    This will:
    1. Find ALL stuck recipients (pending with queued_at set) - regardless of campaign status
    2. Re-queue them to Redis directly
    3. Clear orphaned processing items
    4. Find and re-queue stuck auto-replies
    """
    results = ResyncResult()
    template_engine = TemplateEngine()
    
    try:
        redis_client = get_redis_client()
        
        # 1. Find ALL stuck recipients - regardless of campaign status
        # These are recipients with PENDING status but queued_at is set (meaning they were queued but never sent)
        stuck_recipients = db.query(CampaignRecipient).filter(
            CampaignRecipient.status == RecipientSendStatus.PENDING,
            CampaignRecipient.queued_at.isnot(None)
        ).all()
        
        # Get unique campaign IDs
        campaign_ids = set(cr.campaign_id for cr in stuck_recipients)
        results.campaigns_checked = len(campaign_ids)
        
        # Load campaigns, inboxes, and recipients for re-queuing
        campaigns = {c.id: c for c in db.query(Campaign).filter(Campaign.id.in_(campaign_ids)).all()} if campaign_ids else {}
        
        for cr in stuck_recipients:
            try:
                campaign = campaigns.get(cr.campaign_id)
                if not campaign:
                    continue
                
                # Get inbox
                inbox = db.query(Inbox).filter(Inbox.id == cr.inbox_id).first()
                if not inbox:
                    cr.status = RecipientSendStatus.FAILED
                    cr.error_message = "Inbox not found during resync"
                    continue
                
                # Get recipient
                recipient = db.query(Recipient).filter(Recipient.id == cr.recipient_id).first()
                if not recipient:
                    cr.status = RecipientSendStatus.FAILED
                    cr.error_message = "Recipient not found during resync"
                    continue
                
                # Render template
                recipient_data = {
                    "id": recipient.id,
                    "first_name": recipient.first_name or "",
                    "last_name": recipient.last_name or "",
                    "email": recipient.email,
                    "company": recipient.company or "",
                    "title": recipient.title or "",
                    "unsubscribe_token": recipient.unsubscribe_token or "",
                }
                
                rendered_subject = template_engine.render(
                    template=campaign.subject,
                    recipient=recipient_data
                )
                
                rendered_html = template_engine.render_html(
                    template=campaign.body_html,
                    recipient=recipient_data,
                    campaign_id=campaign.id,
                    include_tracking=campaign.track_opens,
                    include_unsubscribe=True
                )
                
                rendered_text = None
                if campaign.body_text:
                    rendered_text = template_engine.render(
                        template=campaign.body_text,
                        recipient=recipient_data
                    )
                
                # Create email data for queue
                email_data = {
                    "_id": f"campaign:{campaign.id}:recipient:{cr.id}",
                    "smtp_account_id": inbox.smtp_account_id,
                    "inbox_id": inbox.id,
                    "to_email": recipient.email,
                    "from_email": inbox.email,
                    "subject": rendered_subject,
                    "body_html": rendered_html,
                    "body_text": rendered_text,
                    "campaign_id": campaign.id,
                    "recipient_id": cr.recipient_id,
                    "email_type": "campaign",
                }
                
                # Add directly to Redis queue
                email_queue.add(email_data, Priority.NORMAL)
                results.recipients_requeued += 1
                
            except Exception as e:
                results.errors.append(f"Failed to requeue recipient {cr.id}: {str(e)}")
        
        # 2. Clear orphaned processing items
        try:
            processing_count = redis_client.hlen("email:processing")
            if processing_count > 0:
                redis_client.delete("email:processing")
                results.orphaned_processing_cleared = processing_count
        except Exception as e:
            results.errors.append(f"Failed to clear processing: {str(e)}")
        
        # 3. Find and re-queue stuck auto-replies (pending or stuck status, not sent)
        stuck_auto_replies = db.query(CampaignAutoReply).filter(
            CampaignAutoReply.status.in_(["pending", "stuck", "queued"]),
            CampaignAutoReply.sent_at.is_(None)
        ).all()
        
        for ar in stuck_auto_replies:
            try:
                # Get inbox
                inbox = db.query(Inbox).filter(Inbox.id == ar.inbox_id).first()
                if not inbox:
                    ar.status = "failed"
                    ar.error_message = "Inbox not found during resync"
                    continue
                
                # Set status to queued before adding to queue
                ar.status = "queued"
                
                # Create email data
                email_data = {
                    "_id": f"auto_reply:{ar.id}",
                    "smtp_account_id": inbox.smtp_account_id,
                    "inbox_id": inbox.id,
                    "to_email": ar.to_email,
                    "from_email": ar.from_email,
                    "subject": ar.subject,
                    "body_text": ar.body_text,
                    "body_html": ar.body_html,
                    "email_type": "campaign_auto_reply",
                    "auto_reply_id": ar.id,
                }
                
                # Add to queue with high priority
                email_queue.add(email_data, Priority.HIGH)
                results.auto_replies_requeued += 1
                
            except Exception as e:
                ar.status = "failed"
                ar.error_message = str(e)
                results.errors.append(f"Failed to requeue auto-reply {ar.id}: {str(e)}")
        
        db.commit()
        
        return {
            "success": True,
            "campaigns_checked": results.campaigns_checked,
            "recipients_requeued": results.recipients_requeued,
            "orphaned_processing_cleared": results.orphaned_processing_cleared,
            "auto_replies_requeued": results.auto_replies_requeued,
            "errors": results.errors
        }
        
    except Exception as e:
        db.rollback()
        return {
            "success": False,
            "error": str(e),
            "campaigns_checked": results.campaigns_checked,
            "recipients_requeued": results.recipients_requeued,
            "orphaned_processing_cleared": results.orphaned_processing_cleared,
            "auto_replies_requeued": results.auto_replies_requeued,
            "errors": results.errors + [str(e)]
        }


@router.get("/health")
def system_health(
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """
    Get system health status.
    
    Returns status of all components.
    """
    health = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "components": {}
    }
    
    # Check database
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        health["components"]["database"] = {"status": "healthy"}
    except Exception as e:
        health["components"]["database"] = {"status": "critical", "error": str(e)}
        health["status"] = "critical"
    
    # Check Redis
    try:
        redis_client = get_redis_client()
        redis_client.ping()
        
        # Get queue stats
        queue_stats = {
            "high": redis_client.zcard("email:queue:high"),
            "normal": redis_client.zcard("email:queue:normal"),
            "low": redis_client.zcard("email:queue:low"),
            "processing": redis_client.hlen("email:processing"),
            "dead_letter": redis_client.llen("email:dead_letter")
        }
        
        health["components"]["redis"] = {
            "status": "healthy",
            "queues": queue_stats
        }
    except Exception as e:
        health["components"]["redis"] = {"status": "critical", "error": str(e)}
        health["status"] = "critical"
    
    # Check campaigns
    try:
        running = db.query(Campaign).filter(Campaign.status == CampaignStatus.RUNNING).count()
        stuck_recipients = db.query(CampaignRecipient).filter(
            CampaignRecipient.status == RecipientSendStatus.PENDING,
            CampaignRecipient.queued_at.isnot(None)
        ).count()
        
        health["components"]["campaigns"] = {
            "status": "healthy" if stuck_recipients == 0 else "warning",
            "running_campaigns": running,
            "stuck_recipients": stuck_recipients
        }
        
        if stuck_recipients > 0:
            health["status"] = "warning"
            
    except Exception as e:
        health["components"]["campaigns"] = {"status": "unhealthy", "error": str(e)}
        health["status"] = "degraded"
    
    # Check auto-replies
    try:
        pending_replies = db.query(CampaignAutoReply).filter(
            CampaignAutoReply.status == "pending"
        ).count()
        
        stuck_replies = db.query(CampaignAutoReply).filter(
            CampaignAutoReply.status == "queued",
            CampaignAutoReply.sent_at.is_(None)
        ).count()
        
        health["components"]["auto_replies"] = {
            "status": "healthy" if stuck_replies == 0 else "warning",
            "pending": pending_replies,
            "stuck": stuck_replies
        }
        
        if stuck_replies > 0:
            health["status"] = "warning"
            
    except Exception as e:
        health["components"]["auto_replies"] = {"status": "unhealthy", "error": str(e)}
    
    return health


@router.post("/fix-campaign/{campaign_id}")
def fix_campaign(
    campaign_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Fix a specific stuck campaign.
    
    Resets all PENDING recipients to be re-queued.
    """
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    # Reset all pending recipients
    updated = db.query(CampaignRecipient).filter(
        CampaignRecipient.campaign_id == campaign_id,
        CampaignRecipient.status == RecipientSendStatus.PENDING
    ).update({"queued_at": None}, synchronize_session=False)
    
    # Ensure campaign is running
    if campaign.status != CampaignStatus.RUNNING:
        campaign.status = CampaignStatus.RUNNING
    
    db.commit()
    
    return {
        "success": True,
        "campaign_id": campaign_id,
        "recipients_reset": updated,
        "campaign_status": campaign.status.value
    }


# =============================================================================
# Fallback Reply Email (Global)
# =============================================================================

def _auto_detect_smtp_imap(email: str):
    """Auto-detect SMTP/IMAP host from email domain."""
    domain = email.split("@")[1].lower() if "@" in email else ""
    providers = {
        "gmail.com": {"smtp_host": "smtp.gmail.com", "smtp_port": 587, "imap_host": "imap.gmail.com", "imap_port": 993},
        "googlemail.com": {"smtp_host": "smtp.gmail.com", "smtp_port": 587, "imap_host": "imap.gmail.com", "imap_port": 993},
        "outlook.com": {"smtp_host": "smtp-mail.outlook.com", "smtp_port": 587, "imap_host": "outlook.office365.com", "imap_port": 993},
        "hotmail.com": {"smtp_host": "smtp-mail.outlook.com", "smtp_port": 587, "imap_host": "outlook.office365.com", "imap_port": 993},
        "yahoo.com": {"smtp_host": "smtp.mail.yahoo.com", "smtp_port": 587, "imap_host": "imap.mail.yahoo.com", "imap_port": 993},
        "zoho.com": {"smtp_host": "smtp.zoho.com", "smtp_port": 587, "imap_host": "imap.zoho.com", "imap_port": 993},
    }
    return providers.get(domain, {})


@router.get("/fallback-reply-email")
def get_fallback_reply_email(
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get the global fallback reply email config for the current user."""
    fb = db.query(FallbackReplyEmail).filter(
        FallbackReplyEmail.user_id == current_user.id
    ).first()

    if not fb:
        return {"configured": False}

    return {
        "configured": True,
        "id": fb.id,
        "email": fb.email,
        "smtp_host": fb.smtp_host,
        "smtp_port": fb.smtp_port,
        "imap_host": fb.imap_host,
        "imap_port": fb.imap_port,
        "is_active": fb.is_active,
        "created_at": fb.created_at.isoformat() if fb.created_at else None,
    }


@router.post("/fallback-reply-email")
def set_fallback_reply_email(
    config: FallbackEmailConfig,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Set or update the global fallback reply email."""
    # Auto-detect SMTP/IMAP if not provided
    detected = _auto_detect_smtp_imap(config.email)
    smtp_host = config.smtp_host or detected.get("smtp_host")
    smtp_port = config.smtp_port or detected.get("smtp_port", 587)
    imap_host = config.imap_host or detected.get("imap_host")
    imap_port = config.imap_port or detected.get("imap_port", 993)

    if not smtp_host:
        raise HTTPException(status_code=400, detail="Could not auto-detect SMTP host. Please provide it manually.")

    # Test SMTP connection
    import smtplib
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(config.email, config.app_password)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"SMTP login failed: {str(e)}")

    # Upsert
    fb = db.query(FallbackReplyEmail).filter(
        FallbackReplyEmail.user_id == current_user.id
    ).first()

    if fb:
        fb.email = config.email
        fb.app_password = config.app_password
        fb.smtp_host = smtp_host
        fb.smtp_port = smtp_port
        fb.imap_host = imap_host
        fb.imap_port = imap_port
        fb.is_active = True
    else:
        fb = FallbackReplyEmail(
            user_id=current_user.id,
            email=config.email,
            app_password=config.app_password,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            imap_host=imap_host,
            imap_port=imap_port,
            is_active=True,
        )
        db.add(fb)

    db.commit()
    db.refresh(fb)

    return {
        "success": True,
        "id": fb.id,
        "email": fb.email,
        "smtp_host": fb.smtp_host,
        "smtp_port": fb.smtp_port,
        "imap_host": fb.imap_host,
        "imap_port": fb.imap_port,
    }


@router.delete("/fallback-reply-email")
def delete_fallback_reply_email(
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Remove the fallback reply email."""
    fb = db.query(FallbackReplyEmail).filter(
        FallbackReplyEmail.user_id == current_user.id
    ).first()

    if not fb:
        raise HTTPException(status_code=404, detail="No fallback email configured")

    db.delete(fb)
    db.commit()
    return {"success": True}


# =============================================================================
# Kill Switch — blocks ALL email sending globally
# =============================================================================

@router.get("/kill-switch/status")
def kill_switch_status(current_user: User = Depends(require_auth)):
    """Check if the kill switch is enabled."""
    enabled = is_kill_switch_enabled()
    return {
        "enabled": enabled,
        "message": KILL_SWITCH_MESSAGE if enabled else "Sending is allowed"
    }


@router.post("/kill-switch/enable")
def kill_switch_enable(current_user: User = Depends(require_admin)):
    """Enable kill switch — blocks all campaign starts and worker sending."""
    enable_kill_switch()
    return {
        "success": True,
        "enabled": True,
        "message": f"Kill switch ENABLED. {KILL_SWITCH_MESSAGE}"
    }


@router.post("/kill-switch/disable")
def kill_switch_disable(current_user: User = Depends(require_admin)):
    """Disable kill switch — allows sending again."""
    disable_kill_switch()
    return {
        "success": True,
        "enabled": False,
        "message": "Kill switch DISABLED. Sending is now allowed."
    }
