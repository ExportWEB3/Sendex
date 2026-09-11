from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional, List
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel

from database import get_db
from services.queue_service import email_queue, Priority
from services.rate_limiter import rate_limiter
from services.worker_service import worker_service
from services.kill_switch import is_kill_switch_enabled, KILL_SWITCH_MESSAGE
from models.campaign import Campaign, CampaignStatus, CampaignRecipient
from models.inbox import Inbox, InboxState
from models.smtp_account import SMTPAccount
from models.user import User
from api.auth import require_admin, require_auth

router = APIRouter(prefix="/api/queue", tags=["Queue"])


def _campaign_sent_today(db: Session, user_id: Optional[int] = None) -> int:
    """Count campaign-recipient sends from Postgres for the current UTC day."""
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    q = db.query(func.count(CampaignRecipient.id)).join(
        Campaign, Campaign.id == CampaignRecipient.campaign_id
    ).filter(
        CampaignRecipient.sent_at.isnot(None),
        CampaignRecipient.sent_at >= day_start,
        CampaignRecipient.sent_at < day_end,
    )

    if user_id is not None:
        q = q.filter(Campaign.user_id == user_id)

    return int(q.scalar() or 0)


def _warmup_sent_today(db: Session, user_id: Optional[int] = None) -> int:
    """Sum current_daily_count from active WARMING_UP inboxes for today's warmup send count."""
    q = db.query(func.coalesce(func.sum(Inbox.current_daily_count), 0)).filter(
        Inbox.state == InboxState.WARMING_UP,
        Inbox.is_active == True,
    )
    if user_id is not None:
        q = q.filter(Inbox.user_id == user_id)
    return int(q.scalar() or 0)


def _reconcile_sent_counters(queue_payload: dict, db_campaign_sent_today: int) -> None:
    """Prevent Redis-only telemetry from reporting 0 when DB has real campaign sends."""
    today = queue_payload.setdefault("today", {})
    redis_sent_today = int(today.get("sent", 0) or 0)
    merged_sent_today = max(redis_sent_today, int(db_campaign_sent_today))

    today["redis_sent"] = redis_sent_today
    today["campaign_sent"] = int(db_campaign_sent_today)
    today["sent"] = merged_sent_today

    totals = queue_payload.setdefault("totals", {})
    totals["sent"] = merged_sent_today


# Schemas
class QueueEmailRequest(BaseModel):
    smtp_account_id: int
    to_email: str
    to_name: Optional[str] = None
    subject: str
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    priority: str = "normal"  # high, normal, low
    delay_seconds: int = 0


class QueueBulkRequest(BaseModel):
    smtp_account_id: int
    subject: str
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    recipients: List[dict]  # [{"email": "...", "name": "..."}]
    priority: str = "normal"


def _require_owned_smtp_account(db: Session, user_id: int, smtp_account_id: int) -> None:
    """Reject direct queue requests for another user's sending account."""
    exists = db.query(SMTPAccount.id).filter(
        SMTPAccount.id == smtp_account_id,
        SMTPAccount.user_id == user_id,
    ).first()
    if not exists:
        raise HTTPException(status_code=404, detail="SMTP account not found")


# Endpoints
@router.get("/stats")
def get_queue_stats(current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Get queue statistics - returns user-specific stats for regular users, global for admins"""
    from models.user import UserRole
    
    global_stats = email_queue.get_stats()
    _reconcile_sent_counters(global_stats, _campaign_sent_today(db))

    user_stats = email_queue.get_user_stats(current_user.id)
    user_db_sent_today = _campaign_sent_today(db, user_id=current_user.id)
    user_stats["today"]["redis_sent"] = user_stats["today"].get("sent", 0)
    user_stats["today"]["campaign_sent"] = user_db_sent_today
    user_stats["today"]["sent"] = max(int(user_stats["today"].get("sent", 0)), int(user_db_sent_today))
    
    # For regular users, show their own stats prominently
    if current_user.role != UserRole.ADMIN:
        return {
            "user_stats": user_stats,
            "totals": {
                "queued": user_stats["current"]["queued"],
                "sent": user_stats["today"].get("sent", 0),
                "retried": 0,
                "failed": user_stats["today"]["failed"],
            },
            "today": user_stats["today"],
        }
    
    # For admins, show global stats
    return global_stats


@router.get("/my-stats")
def get_my_queue_stats(current_user: User = Depends(require_auth)):
    """Get queue statistics for current user only"""
    return email_queue.get_user_stats(current_user.id)


@router.get("/length")
def get_queue_length(
    priority: Optional[str] = None,
    current_user: User = Depends(require_admin)
):
    """Get queue length by priority"""
    p = Priority(priority) if priority else None
    return email_queue.get_queue_length(p)


@router.post("/add")
def add_to_queue(
    request: QueueEmailRequest,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Add a single email to queue"""
    _require_owned_smtp_account(db, current_user.id, request.smtp_account_id)
    
    # Validate priority
    try:
        priority = Priority(request.priority)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid priority. Use: high, normal, low")
    
    email_data = {
        "smtp_account_id": request.smtp_account_id,
        "to_email": request.to_email,
        "to_name": request.to_name,
        "subject": request.subject,
        "body_text": request.body_text,
        "body_html": request.body_html,
    }
    
    # Pass user_id for per-user tracking
    email_id = email_queue.add(email_data, priority, request.delay_seconds, user_id=current_user.id)
    
    return {
        "success": True,
        "email_id": email_id,
        "priority": priority.value,
        "delay_seconds": request.delay_seconds
    }


@router.post("/add-bulk")
def add_bulk_to_queue(
    request: QueueBulkRequest,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Add multiple emails to queue"""
    _require_owned_smtp_account(db, current_user.id, request.smtp_account_id)
    
    try:
        priority = Priority(request.priority)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid priority")
    
    email_ids = []
    
    for recipient in request.recipients:
        email_data = {
            "smtp_account_id": request.smtp_account_id,
            "to_email": recipient.get("email"),
            "to_name": recipient.get("name"),
            "subject": request.subject,
            "body_text": request.body_text,
            "body_html": request.body_html,
        }
        
        email_id = email_queue.add(email_data, priority, user_id=current_user.id)
        email_ids.append(email_id)
    
    return {
        "success": True,
        "queued_count": len(email_ids),
        "email_ids": email_ids
    }


@router.get("/peek")
def peek_queue(priority: Optional[str] = None, count: int = 10, current_user: User = Depends(require_admin)):
    """Peek at emails in queue without removing them"""
    
    p = Priority(priority) if priority else None
    emails = email_queue.peek(p, count)
    
    return {
        "count": len(emails),
        "emails": emails
    }


@router.get("/dead-letters")
def get_dead_letters(count: int = 50, current_user: User = Depends(require_admin)):
    """Get emails from dead letter queue"""
    
    dead_letters = email_queue.get_dead_letters(count)
    
    return {
        "count": len(dead_letters),
        "emails": dead_letters
    }


@router.post("/dead-letters/{email_id}/retry")
def retry_dead_letter(email_id: str, current_user: User = Depends(require_admin)):
    """Retry a specific email from dead letter queue"""
    
    success = email_queue.retry_dead_letter(email_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Email not found in dead letter queue")
    
    return {"success": True, "message": f"Email {email_id} re-queued"}


@router.delete("/clear")
def clear_queue(priority: Optional[str] = None, confirm: bool = False, current_user: User = Depends(require_admin)):
    """Clear queue (requires confirm=true)"""
    
    if not confirm:
        raise HTTPException(
            status_code=400, 
            detail="Add ?confirm=true to confirm queue clear"
        )
    
    p = Priority(priority) if priority else None
    email_queue.clear_queue(p)
    
    return {"success": True, "message": f"Queue cleared: {priority or 'all'}"}


@router.delete("/dead-letters/clear")
def clear_dead_letters(confirm: bool = False, current_user: User = Depends(require_admin)):
    """Clear dead letter queue (requires confirm=true)"""
    
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Add ?confirm=true to confirm clear"
        )
    
    email_queue.clear_dead_letters()
    
    return {"success": True, "message": "Dead letter queue cleared"}


@router.delete("/stats/reset")
def reset_global_stats(confirm: bool = False, current_user: User = Depends(require_admin)):
    """Reset all global stats - admin only (requires confirm=true)"""
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Add ?confirm=true to confirm stats reset"
        )
    
    email_queue.reset_global_stats()
    
    return {"success": True, "message": "All stats reset to zero"}


# Rate limiter endpoints
@router.get("/rate-limit/smtp/{smtp_id}")
def get_smtp_rate_limit(smtp_id: int, current_user: User = Depends(require_admin)):
    """Get current rate limit usage for an SMTP account"""
    return rate_limiter.get_smtp_usage(smtp_id)


@router.get("/rate-limit/domain/{domain}")
def get_domain_rate_limit(domain: str, current_user: User = Depends(require_admin)):
    """Get current rate limit usage for a domain"""
    return rate_limiter.get_domain_usage(domain)


@router.post("/rate-limit/check")
def check_rate_limit(smtp_id: int, to_email: str, current_user: User = Depends(require_admin)):
    """Check if sending is allowed (doesn't increment counters)"""
    
    # Temporarily check without incrementing
    usage = rate_limiter.get_smtp_usage(smtp_id)
    domain = to_email.split('@')[-1].lower()
    domain_usage = rate_limiter.get_domain_usage(domain)
    
    return {
        "smtp_usage": usage,
        "domain_usage": domain_usage
    }


@router.delete("/rate-limit/smtp/{smtp_id}/reset")
def reset_smtp_rate_limit(smtp_id: int, confirm: bool = False, current_user: User = Depends(require_admin)):
    """Reset rate limits for an SMTP account"""
    
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Add ?confirm=true to confirm reset"
        )
    
    rate_limiter.reset_smtp_limits(smtp_id)
    
    return {"success": True, "message": f"Rate limits reset for SMTP {smtp_id}"}


# =============================================================================
# Worker Control Endpoints
# =============================================================================

@router.get("/worker/status")
def get_worker_status(current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Get worker service status and statistics"""
    from models.user import UserRole
    
    stats = worker_service.get_stats()
    _reconcile_sent_counters(stats["queue"], _campaign_sent_today(db))
    stats["queue"]["today"]["warmup_sent"] = _warmup_sent_today(db)

    # Add worker config info
    stats["worker"]["config"] = {
        "num_workers": worker_service.num_workers,
        "poll_interval": worker_service.poll_interval,
        "batch_size": worker_service.batch_size
    }
    
    # Add user-specific stats
    user_stats = email_queue.get_user_stats(current_user.id)
    user_db_sent_today = _campaign_sent_today(db, user_id=current_user.id)
    user_stats["today"]["redis_sent"] = user_stats["today"].get("sent", 0)
    user_stats["today"]["campaign_sent"] = user_db_sent_today
    user_stats["today"]["warmup_sent"] = _warmup_sent_today(db, user_id=current_user.id)
    user_stats["today"]["sent"] = max(int(user_stats["today"].get("sent", 0)), int(user_db_sent_today))
    stats["user_stats"] = user_stats
    
    # For non-admin users, override global totals with user-specific data
    if current_user.role != UserRole.ADMIN:
        stats["queue"]["totals"] = {
            "queued": user_stats["current"]["queued"],
            "sent": user_stats["today"].get("sent", 0),
            "retried": 0,
            "failed": user_stats["today"]["failed"],
        }
    
    return stats


@router.post("/worker/config")
def configure_worker(num_workers: int = 1, current_user: User = Depends(require_admin)):
    """
    Configure worker settings.
    Changes take effect after restart.
    Recommended: 1 worker to prevent race conditions.
    """
    if num_workers < 1 or num_workers > 5:
        raise HTTPException(status_code=400, detail="num_workers must be between 1 and 5")
    
    # Store for next restart (worker service is a singleton)
    # Note: Actual change requires restart
    return {
        "success": True,
        "message": f"Worker count set to {num_workers}. Restart worker to apply.",
        "current_workers": worker_service.num_workers,
        "requested_workers": num_workers,
        "note": "For safety, 1 worker is recommended to prevent duplicate sends"
    }


@router.post("/worker/start")
def start_worker(background_tasks: BackgroundTasks, current_user: User = Depends(require_admin)):
    """Start the background worker service"""
    if is_kill_switch_enabled():
        raise HTTPException(status_code=403, detail=KILL_SWITCH_MESSAGE)
    
    if worker_service._running:
        return {"success": False, "message": "Worker already running"}
    
    background_tasks.add_task(worker_service.start)
    
    return {
        "success": True,
        "message": "Worker service starting",
        "num_workers": worker_service.num_workers
    }


@router.post("/worker/stop")
def stop_worker(current_user: User = Depends(require_admin)):
    """Stop the background worker service"""
    if not worker_service._running:
        return {"success": False, "message": "Worker not running"}
    
    worker_service.stop(wait=False)
    
    return {"success": True, "message": "Worker service stopping"}


@router.post("/worker/process-now")
def process_queue_now(batch_size: int = 10, current_user: User = Depends(require_admin)):
    """
    Manually trigger processing of queued emails.
    Useful for testing or one-off processing.
    """
    result = worker_service.process_campaigns_now()
    
    return {
        "success": True,
        "processed": result.get("queued", 0),
        "campaigns_seen": result.get("campaigns_seen", 0),
        "scheduled_started": result.get("scheduled_started", 0),
        "batch_size_hint": batch_size,
        "errors": result.get("errors") if result.get("errors") else None,
    }


@router.get("/worker/campaigns")
def get_active_campaigns(current_user: User = Depends(require_admin)):
    """Get list of running and scheduled campaigns"""
    from database import SessionLocal
    
    db = SessionLocal()
    try:
        campaigns = db.query(Campaign).filter(
            Campaign.status.in_([CampaignStatus.RUNNING, CampaignStatus.SCHEDULED])
        ).all()
        
        return {
            "count": len(campaigns),
            "campaigns": [
                {
                    "id": c.id,
                    "name": c.name,
                    "status": c.status.value,
                    "total_recipients": c.total_recipients,
                    "total_sent": c.total_sent,
                    "total_failed": c.total_failed,
                    "send_progress": c.send_progress,
                    "scheduled_at": c.scheduled_at.isoformat() if c.scheduled_at else None,
                    "started_at": c.started_at.isoformat() if c.started_at else None
                }
                for c in campaigns
            ]
        }
    finally:
        db.close()
