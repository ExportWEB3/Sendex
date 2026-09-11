"""
Campaign API Endpoints

Provides REST API for:
- Campaign CRUD operations
- Campaign lifecycle (start, pause, resume, cancel)
- Campaign scheduling
- Statistics and tracking

Multi-tenancy: Each user can only access their own campaigns.
Admins can view any user's campaigns via ?user_id=X parameter.
"""

import logging
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple, Iterable
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, UploadFile, File
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from database import get_db
from models.campaign import CampaignStatus, Campaign, CampaignRecipient, RecipientSendStatus
from models.inbox import Inbox
from models.recipient import Recipient
from models.warmup import CampaignAutoReply, FallbackReplyEmail
from models.smtp_account import SMTPAccount
from models.user import User, UserRole
from services.campaign_service import CampaignService
from services.imap_service import IMAPService
from services.queue_service import email_queue, Priority
from services.attachment_service import (
    normalize_attachment_metadata,
    public_attachment_metadata,
    store_attachment,
)
from api.auth import require_admin, require_auth


router = APIRouter(prefix="/api/campaigns", tags=["Campaigns"])


def get_effective_user_id(current_user: User, requested_user_id: Optional[int] = None) -> int:
    """Get the effective user_id for queries."""
    if requested_user_id is not None and current_user.role == UserRole.ADMIN:
        return requested_user_id
    return current_user.id


def check_campaign_ownership(campaign: Campaign, current_user: User):
    """Check if user has access to campaign. Raises 403 if not."""
    if campaign.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")


def _effective_recipient_template_id(
    campaign: Campaign,
    campaign_recipient: CampaignRecipient,
) -> Optional[int]:
    """Resolve template identity, including legacy one-template campaigns."""
    if campaign_recipient.template_id is not None:
        return campaign_recipient.template_id
    template_ids = campaign.template_ids or []
    if len(template_ids) == 1:
        try:
            return int(template_ids[0])
        except (TypeError, ValueError):
            return None
    return None


def _reconstruct_campaign_recipient_content(
    db: Session,
    campaign: Campaign,
    campaign_recipient: CampaignRecipient,
    recipient: Recipient,
    template_id: Optional[int],
) -> Dict[str, Optional[str]]:
    """Re-render historic content when no exact send-time snapshot exists.

    Campaign content is the source for custom and one-template campaigns.
    Rotation campaigns use the recorded per-recipient template when available.
    The response explicitly identifies this as a reconstruction.
    """
    import re as html_re
    from html import escape

    from models.ses_template import SESEmailTemplate
    from services.template_engine import TemplateEngine

    subject_template = campaign.subject or ""
    html_template = campaign.body_html or ""
    text_template = campaign.body_text

    if template_id is not None and len(campaign.template_ids or []) >= 2:
        template = db.query(SESEmailTemplate).filter(
            SESEmailTemplate.id == template_id,
            SESEmailTemplate.user_id == campaign.user_id,
        ).first()
        if template:
            subject_template = template.subject_line or subject_template
            html_template = template.html_content or html_template
            text_template = template.text_fallback or text_template

    if html_template and not html_re.search(
        r"<\s*(p|div|br|table|html|head|body|span|a|img|ul|ol|li|h[1-6])\b",
        html_template,
        html_re.IGNORECASE,
    ):
        html_template = escape(html_template).replace("\n", "<br>\n")

    recipient_data = {
        "id": recipient.id,
        "first_name": recipient.first_name or "",
        "last_name": recipient.last_name or "",
        "email": recipient.email,
        "company": recipient.company or "",
        "title": recipient.title or "",
        "unsubscribe_token": recipient.unsubscribe_token or "",
    }

    extra_context = None
    if campaign.template_data:
        extra_context = dict(campaign.template_data)
        per_template = extra_context.pop("__per_template__", None)
        if per_template and template_id is not None:
            extra_context.update(per_template.get(str(template_id), {}) or {})

    inbox = None
    smtp_account = None
    if campaign_recipient.inbox_id:
        inbox = db.query(Inbox).filter(Inbox.id == campaign_recipient.inbox_id).first()
        if inbox and inbox.smtp_account_id:
            smtp_account = db.query(SMTPAccount).filter(
                SMTPAccount.id == inbox.smtp_account_id
            ).first()

    sender_email = (
        getattr(smtp_account, "from_email", None)
        or getattr(inbox, "email", None)
        or ""
    )
    fallback_sender_name = (
        inbox.email.split("@", 1)[0].replace(".", " ").title()
        if inbox and inbox.email
        else ""
    )
    engine = TemplateEngine()
    sender_context = engine.resolve_sender_context(
        recipient=recipient_data,
        sender_email=sender_email,
        sender_name_template=getattr(smtp_account, "from_name", None),
        extra_context=extra_context,
        fallback_sender_name=fallback_sender_name,
    )

    return {
        "sent_subject": engine.render(
            template=subject_template,
            recipient=recipient_data,
            sender=sender_context,
            extra_context=extra_context,
        ),
        "sent_body_html": engine.render_html(
            template=html_template,
            recipient=recipient_data,
            sender=sender_context,
            campaign_id=campaign.id,
            include_tracking=campaign.track_opens,
            include_unsubscribe=True,
            extra_context=extra_context,
        ),
        "sent_body_text": (
            engine.render(
                template=text_template,
                recipient=recipient_data,
                sender=sender_context,
                extra_context=extra_context,
            )
            if text_template
            else None
        ),
        "sent_from_email": sender_email or None,
        "sent_from_name": sender_context.get("from_name") or None,
    }


def _worker_status_key(send_timezone: str) -> str:
    return f"worker:{send_timezone.replace('/', '_').lower()}:status"


def _load_worker_status_map(redis_client, timezones: Iterable[str]) -> Dict[str, Optional[Dict[str, Any]]]:
    """Load worker status payloads for the requested timezone set."""
    status_map: Dict[str, Optional[Dict[str, Any]]] = {}
    for tz in {tz for tz in timezones if tz}:
        try:
            raw = redis_client.get(_worker_status_key(tz))
            if not raw:
                status_map[tz] = None
                continue
            parsed = json.loads(raw)
            status_map[tz] = parsed if isinstance(parsed, dict) else None
        except Exception:
            status_map[tz] = None
    return status_map


def _effective_campaign_status(
    campaign: Campaign,
    worker_status_map: Dict[str, Optional[Dict[str, Any]]],
) -> Tuple[str, Optional[str], Optional[str], Optional[str]]:
    """Compute UI-facing campaign state based on worker runtime posture."""
    effective_status = campaign.status.value
    effective_reason: Optional[str] = None
    next_window_open: Optional[str] = None
    worker_runtime_status: Optional[str] = None

    if campaign.status != CampaignStatus.RUNNING or not campaign.send_timezone:
        return effective_status, effective_reason, next_window_open, worker_runtime_status

    worker_info = worker_status_map.get(campaign.send_timezone)
    if not worker_info:
        return effective_status, effective_reason, next_window_open, worker_runtime_status

    worker_runtime_status = str(worker_info.get("status") or "").lower() or None
    business_hours = worker_info.get("business_hours")
    next_window_open = worker_info.get("next_window_open")
    local_time = worker_info.get("local_time")

    if worker_runtime_status == "cooldown" or business_hours is False:
        effective_status = "cooldown"
        if next_window_open:
            effective_reason = (
                f"{campaign.send_timezone} worker is outside business hours "
                f"({local_time or 'local time unavailable'}). Next window: {next_window_open}."
            )
        else:
            effective_reason = (
                f"{campaign.send_timezone} worker is outside business hours "
                f"({local_time or 'local time unavailable'})."
            )

    return effective_status, effective_reason, next_window_open, worker_runtime_status


# =============================================================================
# Pydantic Schemas
# =============================================================================

class CampaignCreate(BaseModel):
    """Schema for creating a new campaign."""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "name": "March Newsletter",
            "subject": "Hi {{first_name}}, check out our latest updates!",
            "body_html": "<h1>Hello {{first_name}}!</h1><p>We have exciting news...</p>",
            "list_id": 1,
            "inbox_ids": [1, 2, 3],
            "track_opens": True,
            "track_clicks": True,
            "template_data": {"company_name": "Acme Inc"},
        }
    })

    name: str
    subject: str
    body_html: str
    body_text: Optional[str] = None
    list_id: int
    inbox_ids: List[int]
    reply_to_email: Optional[EmailStr] = None
    track_opens: bool = True
    track_clicks: bool = True
    template_data: Optional[dict] = None
    scheduled_at: Optional[datetime] = None
    attachments: Optional[List[dict]] = None  # [{filename, stored_name, filepath, content_type, size}]
    template_ids: Optional[List[int]] = None  # Multi-template rotation: email-template IDs
    send_timezone: Optional[str] = None  # 'US/Eastern' or 'US/Pacific'
    
class CampaignUpdate(BaseModel):
    """Schema for updating a campaign."""
    name: Optional[str] = None
    subject: Optional[str] = None
    body_html: Optional[str] = None
    body_text: Optional[str] = None
    inbox_ids: Optional[List[int]] = None
    reply_to_email: Optional[EmailStr] = None
    track_opens: Optional[bool] = None
    track_clicks: Optional[bool] = None
    template_data: Optional[dict] = None
    template_ids: Optional[List[int]] = None  # Multi-template rotation
    send_timezone: Optional[str] = None  # 'US/Eastern' or 'US/Pacific'


class CampaignSchedule(BaseModel):
    """Schema for scheduling a campaign."""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "scheduled_at": "2024-03-15T10:00:00Z",
        }
    })

    scheduled_at: datetime


class PauseRequest(BaseModel):
    """Schema for pause request."""
    reason: Optional[str] = None


# =============================================================================
# Campaign CRUD Endpoints
# =============================================================================

@router.post("/upload-attachment")
async def upload_attachment(
    file: UploadFile = File(...),
    current_user: User = Depends(require_auth),
):
    """Upload a file attachment for campaigns. Returns file metadata."""
    content = await file.read()
    try:
        metadata = store_attachment(content, file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, **public_attachment_metadata([metadata])[0]}


@router.post("")
def create_campaign(
    data: CampaignCreate,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """
    Create a new email campaign.
    
    The campaign is created in DRAFT status (or SCHEDULED if scheduled_at provided).
    Use the /start endpoint to begin sending.
    
    Template variables supported:
    - {{first_name}} - Recipient's first name
    - {{last_name}} - Recipient's last name
    - {{email}} - Recipient's email
    - {{company}} - Recipient's company
    - {{title}} - Recipient's job title
    - {{unsubscribe_link}} - Auto-generated unsubscribe link
    - Any custom variables from template_data
    """
    service = CampaignService(db)
    
    try:
        # Validate send_timezone
        valid_timezones = {'US/Eastern', 'US/Pacific'}
        send_tz = data.send_timezone
        if send_tz and send_tz not in valid_timezones:
            raise HTTPException(status_code=400, detail=f"Invalid timezone. Must be one of: {valid_timezones}")
        
        campaign = service.create_campaign(
            name=data.name,
            subject=data.subject,
            body_html=data.body_html,
            body_text=data.body_text,
            list_id=data.list_id,
            inbox_ids=data.inbox_ids,
            reply_to_email=data.reply_to_email,
            track_opens=data.track_opens,
            track_clicks=data.track_clicks,
            template_data=data.template_data,
            scheduled_at=data.scheduled_at,
            user_id=current_user.id,
            attachments=normalize_attachment_metadata(data.attachments),
            template_ids=data.template_ids,
            send_timezone=send_tz
        )
        
        return {
            "success": True,
            "campaign_id": campaign.id,
            "name": campaign.name,
            "status": campaign.status.value,
            "message": "Campaign created successfully"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
def list_campaigns(
    status: Optional[str] = Query(None, description="Filter by status: draft, scheduled, running, paused, completed, cancelled, failed"),
    user_id: Optional[int] = Query(None, description="Admin only: view another user's campaigns"),
    limit: int = Query(50, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """List all campaigns with optional status filter."""
    effective_user_id = get_effective_user_id(current_user, user_id)
    
    # Convert status string to enum if provided
    status_enum = None
    if status:
        try:
            status_enum = CampaignStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Must be one of: {[s.value for s in CampaignStatus]}"
            )
    
    # Build query with user_id filter
    query = db.query(Campaign).filter(Campaign.user_id == effective_user_id)
    
    if status_enum:
        query = query.filter(Campaign.status == status_enum)
    
    campaigns = query.order_by(Campaign.created_at.desc()).limit(limit).offset(offset).all()

    worker_status_map = _load_worker_status_map(
        email_queue.redis,
        [c.send_timezone for c in campaigns if c.send_timezone],
    )
    
    # Pre-load inbox info for all campaigns (including deleted inboxes info)
    all_inbox_ids = set()
    for c in campaigns:
        all_inbox_ids.update(c.inbox_ids or [])
    
    existing_inboxes = {}
    if all_inbox_ids:
        for inbox in db.query(Inbox).filter(Inbox.id.in_(list(all_inbox_ids))).all():
            existing_inboxes[inbox.id] = inbox.email
    
    payload = []
    for c in campaigns:
        effective_status, effective_reason, next_window_open, worker_runtime_status = _effective_campaign_status(
            c,
            worker_status_map,
        )
        payload.append(
            {
                "id": c.id,
                "name": c.name,
                "subject": c.subject,
                "body_html": c.body_html,
                "body_text": c.body_text,
                "status": c.status.value,
                "list_id": c.list_id,
                "inbox_ids": c.inbox_ids or [],
                "inbox_emails": {str(iid): existing_inboxes.get(iid) for iid in (c.inbox_ids or [])},
                "reply_to_email": c.reply_to_email,
                "total_recipients": c.total_recipients,
                "total_sent": c.total_sent,
                "total_failed": c.total_failed,
                "total_opens": c.total_opens,
                "total_clicks": c.total_clicks,
                "total_replies": c.total_replies,
                "attachments": public_attachment_metadata(c.attachments),
                "template_ids": c.template_ids,
                "scheduled_at": c.scheduled_at.isoformat() if c.scheduled_at else None,
                "started_at": c.started_at.isoformat() if c.started_at else None,
                "completed_at": c.completed_at.isoformat() if c.completed_at else None,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "send_timezone": c.send_timezone,
                "effective_status": effective_status,
                "effective_reason": effective_reason,
                "next_window_open": next_window_open,
                "worker_runtime_status": worker_runtime_status,
            }
        )

    return payload


@router.get("/all-auto-replies")
def get_all_auto_replies(
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Get all auto-replies across all campaigns, grouped by campaign."""
    from datetime import timedelta
    
    recent_date = datetime.now(timezone.utc) - timedelta(days=30)
    
    replies_query = db.query(CampaignAutoReply).join(
        Campaign, CampaignAutoReply.campaign_id == Campaign.id
    ).filter(
        Campaign.started_at >= recent_date
    )
    if current_user.role != UserRole.ADMIN:
        replies_query = replies_query.filter(Campaign.user_id == current_user.id)
    replies = replies_query.order_by(CampaignAutoReply.scheduled_at.desc()).all()
    
    # Get campaign names
    campaign_ids = list(set(r.campaign_id for r in replies))
    campaigns_map = {}
    if campaign_ids:
        for c in db.query(Campaign).filter(Campaign.id.in_(campaign_ids)).all():
            campaigns_map[c.id] = c.name
    
    grouped = {}
    for r in replies:
        cid = r.campaign_id
        if cid not in grouped:
            grouped[cid] = {
                "campaign_id": cid,
                "campaign_name": campaigns_map.get(cid, f"Campaign #{cid}"),
                "replies": []
            }
        grouped[cid]["replies"].append({
            "id": r.id,
            "to_email": r.to_email,
            "from_email": r.from_email,
            "subject": r.subject,
            "original_reply_subject": r.original_reply_subject,
            "original_reply_snippet": r.original_reply_snippet,
            "body_text": r.body_text,
            "status": r.status,
            "scheduled_at": r.scheduled_at.isoformat() if r.scheduled_at else None,
            "sent_at": r.sent_at.isoformat() if r.sent_at else None,
            "error_message": r.error_message,
        })
    
    return {
        "total": len(replies),
        "campaigns": list(grouped.values())
    }


@router.get("/{campaign_id}")
def get_campaign(
    campaign_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get campaign details by ID."""
    service = CampaignService(db)
    campaign = service.get_campaign(campaign_id)
    
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    check_campaign_ownership(campaign, current_user)

    worker_status_map = _load_worker_status_map(
        email_queue.redis,
        [campaign.send_timezone] if campaign.send_timezone else [],
    )
    effective_status, effective_reason, next_window_open, worker_runtime_status = _effective_campaign_status(
        campaign,
        worker_status_map,
    )
    
    return {
        "id": campaign.id,
        "name": campaign.name,
        "subject": campaign.subject,
        "body_html": campaign.body_html,
        "body_text": campaign.body_text,
        "status": campaign.status.value,
        "list_id": campaign.list_id,
        "inbox_ids": campaign.inbox_ids,
        "reply_to_email": campaign.reply_to_email,
        "track_opens": campaign.track_opens,
        "track_clicks": campaign.track_clicks,
        "template_data": campaign.template_data,
        "total_recipients": campaign.total_recipients,
        "total_sent": campaign.total_sent,
        "total_failed": campaign.total_failed,
        "total_opens": campaign.total_opens,
        "total_clicks": campaign.total_clicks,
        "total_bounces": campaign.total_bounces,
        "total_unsubscribes": campaign.total_unsubscribes,
        "send_progress": campaign.send_progress,
        "pause_reason": campaign.pause_reason,
        "template_ids": campaign.template_ids,
        "template_rotation_state": campaign.template_rotation_state,
        "scheduled_at": campaign.scheduled_at.isoformat() if campaign.scheduled_at else None,
        "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
        "completed_at": campaign.completed_at.isoformat() if campaign.completed_at else None,
        "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
        "send_timezone": campaign.send_timezone,
        "effective_status": effective_status,
        "effective_reason": effective_reason,
        "next_window_open": next_window_open,
        "worker_runtime_status": worker_runtime_status,
    }


@router.put("/{campaign_id}")
def update_campaign(
    campaign_id: int,
    data: CampaignUpdate,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """
    Update a campaign. Only allowed in DRAFT or SCHEDULED status.
    """
    service = CampaignService(db)
    
    # Check ownership first
    campaign = service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    check_campaign_ownership(campaign, current_user)
    
    try:
        # Get only non-None values
        updates = {k: v for k, v in data.model_dump().items() if v is not None}
        
        if not updates:
            raise HTTPException(status_code=400, detail="No updates provided")
        
        campaign = service.update_campaign(campaign_id, **updates)
        
        return {
            "success": True,
            "message": "Campaign updated successfully",
            "campaign_id": campaign.id,
            "status": campaign.status.value
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{campaign_id}")
def delete_campaign(
    campaign_id: int,
    force: str = Query("false"),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Delete a campaign. Only allowed in DRAFT status unless force=true."""
    service = CampaignService(db)
    
    # Check ownership first
    campaign = service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    check_campaign_ownership(campaign, current_user)
    
    # Convert string to boolean
    force_delete = force.lower() in ('true', '1', 'yes')
    
    try:
        service.delete_campaign(campaign_id, force=force_delete)
        
        return {
            "success": True,
            "message": "Campaign deleted successfully"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Failed to delete campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete campaign")


# =============================================================================
# Campaign Lifecycle Endpoints
# =============================================================================

@router.post("/pause-all")
def pause_all_campaigns(
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """
    Pause every RUNNING campaign owned by the current user.
    """
    service = CampaignService(db)
    result = service.pause_all_campaigns(user_id=current_user.id)

    return {
        "success": True,
        "paused": result["paused"],
        "failed": result["failed"],
        "paused_count": len(result["paused"]),
        "failed_count": len(result["failed"]),
    }


@router.post("/start-all")
def start_all_campaigns(
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """
    Start every DRAFT/SCHEDULED campaign owned by the current user.

    All campaigns become RUNNING immediately, but their first micro-batch due
    times are staggered evenly across START_ALL_STAGGER_SECONDS (default 900s)
    so the fleet ignites smoothly instead of all firing at once.
    """
    service = CampaignService(db)
    result = service.start_all_campaigns(user_id=current_user.id)

    return {
        "success": True,
        "started": result["started"],
        "failed": result["failed"],
        "started_count": len(result["started"]),
        "failed_count": len(result["failed"]),
        "stagger_window_seconds": result["stagger_window_seconds"],
    }


@router.post("/{campaign_id}/start")
def start_campaign(
    campaign_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """
    Start a campaign.
    
    This will:
    1. Queue all recipients from the linked list
    2. Set status to RUNNING
    3. Begin processing in the background
    
    Use GET /campaigns/{id}/stats to monitor progress.
    """
    service = CampaignService(db)
    
    # Check ownership first
    campaign = service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    check_campaign_ownership(campaign, current_user)
    
    try:
        campaign = service.start_campaign(campaign_id)
        
        return {
            "success": True,
            "message": "Campaign started",
            "campaign_id": campaign.id,
            "status": campaign.status.value,
            "recipients_queued": campaign.total_recipients,
            "send_progress": campaign.send_progress
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{campaign_id}/pause")
def pause_campaign(
    campaign_id: int,
    data: Optional[PauseRequest] = None,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """
    Pause a running campaign.
    
    Sending will stop after the current batch completes.
    Use /resume to continue.
    """
    service = CampaignService(db)
    
    # Check ownership first
    campaign = service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    check_campaign_ownership(campaign, current_user)
    
    reason = data.reason if data else None
    
    try:
        campaign = service.pause_campaign(campaign_id, reason)
        
        return {
            "success": True,
            "message": "Campaign paused",
            "campaign_id": campaign.id,
            "status": campaign.status.value,
            "send_progress": campaign.send_progress
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{campaign_id}/resume")
def resume_campaign(
    campaign_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Resume a paused campaign."""
    service = CampaignService(db)
    
    # Check ownership first
    campaign = service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    check_campaign_ownership(campaign, current_user)
    
    try:
        campaign = service.resume_campaign(campaign_id)
        
        return {
            "success": True,
            "message": "Campaign resumed",
            "campaign_id": campaign.id,
            "status": campaign.status.value,
            "send_progress": campaign.send_progress
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{campaign_id}/cancel")
def cancel_campaign(
    campaign_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """
    Cancel a campaign.
    
    This permanently stops the campaign. Use pause if you may want to resume.
    """
    service = CampaignService(db)
    
    # Check ownership first
    campaign = service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    check_campaign_ownership(campaign, current_user)
    
    try:
        campaign = service.cancel_campaign(campaign_id)
        
        return {
            "success": True,
            "message": "Campaign cancelled",
            "campaign_id": campaign.id,
            "status": campaign.status.value,
            "final_progress": campaign.send_progress
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{campaign_id}/schedule")
def schedule_campaign(
    campaign_id: int,
    data: CampaignSchedule,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """
    Schedule a campaign to start at a specific time.
    
    The campaign must be in DRAFT status.
    """
    service = CampaignService(db)
    
    # Check ownership first
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    check_campaign_ownership(campaign, current_user)
    
    try:
        campaign = service.update_campaign(
            campaign_id,
            scheduled_at=data.scheduled_at,
            status=CampaignStatus.SCHEDULED
        )
        
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        return {
            "success": True,
            "message": "Campaign scheduled",
            "campaign_id": campaign.id,
            "status": campaign.status.value,
            "scheduled_at": campaign.scheduled_at.isoformat()
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =============================================================================
# Statistics & Processing Endpoints
# =============================================================================

@router.get("/{campaign_id}/recipients")
def get_campaign_recipients(
    campaign_id: int,
    status_filter: Optional[str] = Query(None, description="Filter by status: pending, sent, failed, etc."),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=500),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get all recipients for a campaign with their individual send status."""
    from models.recipient import Recipient
    
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    check_campaign_ownership(campaign, current_user)
    
    query = db.query(CampaignRecipient, Recipient).join(
        Recipient, CampaignRecipient.recipient_id == Recipient.id
    ).filter(
        CampaignRecipient.campaign_id == campaign_id
    )
    
    if status_filter:
        try:
            status_enum = RecipientSendStatus(status_filter)
            query = query.filter(CampaignRecipient.status == status_enum)
        except ValueError:
            pass
    
    total = query.count()
    results = query.order_by(CampaignRecipient.id).offset((page - 1) * per_page).limit(per_page).all()
    
    # Build a template lookup from both current campaign configuration and
    # recipient history so template names survive rotation-state changes.
    effective_template_ids = {
        template_id
        for cr, _recipient in results
        if (template_id := _effective_recipient_template_id(campaign, cr)) is not None
    }
    template_names = {}
    if effective_template_ids:
        from models.ses_template import SESEmailTemplate
        templates = db.query(SESEmailTemplate.id, SESEmailTemplate.name).filter(
            SESEmailTemplate.id.in_(effective_template_ids),
            SESEmailTemplate.user_id == campaign.user_id,
        ).all()
        template_names = {t.id: t.name for t in templates}
    
    recipients_list = []
    for cr, recipient in results:
        template_id = _effective_recipient_template_id(campaign, cr)
        recipients_list.append({
            "id": cr.id,
            "recipient_id": recipient.id,
            "email": recipient.email,
            "first_name": recipient.first_name,
            "last_name": recipient.last_name,
            "status": cr.status.value if cr.status else "pending",
            "error_message": cr.error_message,
            "queued_at": cr.queued_at.isoformat() if cr.queued_at else None,
            "sent_at": cr.sent_at.isoformat() if cr.sent_at else None,
            "opened_at": cr.opened_at.isoformat() if cr.opened_at else None,
            "clicked_at": cr.clicked_at.isoformat() if cr.clicked_at else None,
            "replied_at": cr.replied_at.isoformat() if cr.replied_at else None,
            "bounced_at": cr.bounced_at.isoformat() if cr.bounced_at else None,
            "open_count": cr.open_count or 0,
            "click_count": cr.click_count or 0,
            "template_id": template_id,
            "template_name": template_names.get(template_id) if template_id is not None else None,
            "has_preview": cr.sent_at is not None,
        })
    
    # Count by status
    from sqlalchemy import func as sqlfunc
    status_counts = db.query(
        CampaignRecipient.status,
        sqlfunc.count(CampaignRecipient.id)
    ).filter(
        CampaignRecipient.campaign_id == campaign_id
    ).group_by(CampaignRecipient.status).all()
    
    counts = {}
    for status_val, count in status_counts:
        counts[status_val.value if status_val else "pending"] = count
    
    return {
        "campaign_id": campaign_id,
        "campaign_name": campaign.name,
        "total": total,
        "page": page,
        "per_page": per_page,
        "status_counts": counts,
        "recipients": recipients_list,
    }


@router.get("/{campaign_id}/recipient/{recipient_id}/preview")
def get_campaign_recipient_preview(
    campaign_id: int,
    recipient_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get the sent email preview for a specific campaign recipient.
    
    Returns the subject, HTML body, from address, and template used
    for the email that was sent to this recipient.
    """
    from models.recipient import Recipient
    from models.ses_template import SESEmailTemplate
    
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    check_campaign_ownership(campaign, current_user)
    
    # Get campaign recipient details
    cr = db.query(CampaignRecipient).filter(
        CampaignRecipient.campaign_id == campaign_id,
        CampaignRecipient.id == recipient_id
    ).first()
    
    if not cr:
        raise HTTPException(status_code=404, detail="Recipient not found in campaign")
    
    # Get recipient details
    recipient = db.query(Recipient).filter(Recipient.id == cr.recipient_id).first()
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")
    
    if cr.sent_at is None:
        raise HTTPException(status_code=409, detail="Email has not been sent yet")

    template_id = _effective_recipient_template_id(campaign, cr)

    # Get template name if available
    template_name = None
    if template_id is not None:
        template = db.query(SESEmailTemplate).filter(
            SESEmailTemplate.id == template_id,
            SESEmailTemplate.user_id == campaign.user_id,
        ).first()
        if template:
            template_name = template.name

    has_snapshot = bool(cr.sent_subject is not None and cr.sent_body_html is not None)
    content = {
        "sent_subject": cr.sent_subject,
        "sent_body_html": cr.sent_body_html,
        "sent_body_text": None,
        "sent_from_email": cr.sent_from_email,
        "sent_from_name": cr.sent_from_name,
    }
    if not has_snapshot:
        content = _reconstruct_campaign_recipient_content(
            db,
            campaign,
            cr,
            recipient,
            template_id,
        )
    
    return {
        "campaign_id": campaign_id,
        "campaign_recipient_id": cr.id,
        "recipient_id": cr.recipient_id,
        "email": recipient.email if recipient else None,
        "status": cr.status.value if cr.status else "pending",
        "sent_at": cr.sent_at.isoformat() if cr.sent_at else None,
        "template_id": template_id,
        "template_name": template_name,
        "preview_source": "snapshot" if has_snapshot else "reconstructed",
        **content,
    }


@router.get("/{campaign_id}/stats")
def get_campaign_stats(
    campaign_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Get detailed campaign statistics."""
    service = CampaignService(db)
    campaign = service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    check_campaign_ownership(campaign, current_user)
    stats = service.get_campaign_stats(campaign_id)
    
    if not stats:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    return stats


@router.post("/{campaign_id}/process-batch")
def process_campaign_batch(
    campaign_id: int,
    batch_size: int = Query(10, ge=1, le=100),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Process a batch of recipients for a running campaign.
    
    This is typically called by a background worker, but can be
    triggered manually for testing or manual processing.
    
    Returns the number of emails sent, failed, and remaining.
    """
    service = CampaignService(db)
    
    try:
        result = service.process_campaign_batch(campaign_id, batch_size)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =============================================================================
# Helper: Match reply subject to campaign subject
# =============================================================================

import re as _re

def _strip_reply_prefixes(subject: str) -> str:
    """Strip Re:/Fwd:/Fw: prefixes (possibly nested) from a subject."""
    cleaned = subject.strip()
    while True:
        m = _re.match(r'^(re|fwd|fw)\s*:\s*', cleaned, _re.IGNORECASE)
        if m:
            cleaned = cleaned[m.end():].strip()
        else:
            break
    return cleaned


def _resolve_template_vars(subject: str) -> str:
    """Resolve {{var|default}} template variables to their default values.

    e.g. '{{notification_title|Important Update}}' → 'Important Update'
    Also strips bare {{var}} tags with no default.
    """
    # {{var_name|default value}} → default value
    resolved = _re.sub(r'\{\{[^|{}]+\|([^}]+)\}\}', r'\1', subject)
    # {{var_name}} (no default) → empty string
    resolved = _re.sub(r'\{\{[^}]+\}\}', '', resolved)
    # Clean up any double spaces left behind
    resolved = _re.sub(r'  +', ' ', resolved).strip()
    return resolved


def _strip_template_vars(subject: str) -> str:
    """Strip ALL template variables (including defaults) from subject.
    
    e.g. '🔥 {{discount|25}}% OFF' → '🔥 % OFF'
    This matches what the template engine produces when a variable has no value.
    """
    stripped = _re.sub(r'\{\{[^}]+\}\}', '', subject)
    stripped = _re.sub(r'  +', ' ', stripped).strip()
    return stripped


def _reply_matches_campaign(mail_subject: str, campaign_subject: str,
                            template_subjects: list[str] | None = None) -> bool:
    """
    Check if a reply's subject plausibly references the campaign's subject
    OR any of the template rotation subjects.
    
    Uses structural matching: extracts static text portions around template
    variables and verifies they all appear in order in the reply subject.
    This handles personalized subjects like 'Welcome to {{company}}, {{name}}!'
    where the reply might be 'Re: Welcome to Acme Corp, John!'.
    """
    import re as _re
    reply_core = _strip_reply_prefixes(mail_subject).lower().strip()
    
    if not reply_core:
        return True  # Empty reply subject — can't determine, allow match
    
    # Build list of all possible campaign subjects
    all_subjects = [campaign_subject]
    if template_subjects:
        all_subjects.extend(template_subjects)
    
    for subj in all_subjects:
        stripped_subj = _strip_reply_prefixes(subj)
        
        # Method 1: resolved defaults (handles simple cases)
        campaign_resolved = _resolve_template_vars(stripped_subj).lower().strip()
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
            # No template vars — plain comparison
            campaign_stripped = stripped_subj.lower().strip()
            if campaign_stripped:
                if reply_core == campaign_stripped:
                    return True
                if campaign_stripped in reply_core or reply_core in campaign_stripped:
                    return True
    
    return False


@router.post("/{campaign_id}/check-replies")
def check_campaign_replies(
    campaign_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """
    Check for replies to a campaign by scanning inbox IMAP.
    
    Looks for emails that are replies to campaign subjects.
    """
    from datetime import datetime
    
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    check_campaign_ownership(campaign, current_user)
    
    # Get all inboxes used by this campaign
    inbox_ids = campaign.inbox_ids or []
    if not inbox_ids:
        # Get inboxes from campaign recipients
        recipient_inboxes = db.query(CampaignRecipient.inbox_id).filter(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.inbox_id.isnot(None)
        ).distinct().all()
        inbox_ids = [r[0] for r in recipient_inboxes]
    
    results = {
        "campaign_id": campaign_id,
        "inboxes_checked": 0,
        "replies_found": 0,
        "recipients_updated": 0,
        "errors": [],
        "replies": []
    }
    
    # Get recipient emails for this campaign
    campaign_recipients = db.query(CampaignRecipient, Recipient).join(
        Recipient, CampaignRecipient.recipient_id == Recipient.id
    ).filter(
        CampaignRecipient.campaign_id == campaign_id,
        CampaignRecipient.status == RecipientSendStatus.SENT
    ).all()
    
    # Build dict of recipient emails
    recipient_emails = {}
    # Also build In-Reply-To lookup from stored sent_message_id
    message_id_to_cr = {}  # sent_message_id → CampaignRecipient
    has_stored_ids = False
    for cr, recipient in campaign_recipients:
        recipient_emails[recipient.email.lower()] = cr
        if cr.sent_message_id:
            message_id_to_cr[cr.sent_message_id] = cr
            has_stored_ids = True
    
    # Build set of all system inbox emails to filter out warmup/internal replies
    all_system_inbox_emails = set(
        ib.email.lower() for ib in db.query(Inbox).filter(Inbox.is_active == True).all()
    )
    
    if not recipient_emails:
        return {"message": "No sent recipients to check replies for", **results}
    
    # Build list of inboxes to check (including reply-to inboxes)
    inboxes_to_check = set()
    for inbox_id in inbox_ids:
        inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
        if not inbox:
            continue
        if inbox.reply_enabled and inbox.imap_host and inbox.imap_username:
            inboxes_to_check.add(inbox.id)
        if inbox.reply_to_email:
            reply_to_inbox = db.query(Inbox).filter(
                Inbox.email == inbox.reply_to_email,
                Inbox.reply_enabled == True
            ).first()
            if reply_to_inbox and reply_to_inbox.imap_host and reply_to_inbox.imap_username:
                inboxes_to_check.add(reply_to_inbox.id)
    
    # Check each inbox
    for check_inbox_id in inboxes_to_check:
        inbox = db.query(Inbox).filter(Inbox.id == check_inbox_id).first()
        if not inbox or not inbox.reply_enabled or not inbox.imap_host or not inbox.imap_username:
            results["errors"].append(f"Inbox {check_inbox_id}: Replies not enabled or IMAP not configured")
            continue
        
        try:
            imap = IMAPService(
                inbox.imap_host,
                inbox.imap_port,
                inbox.imap_username,
                inbox.imap_password,
                True  # SSL enabled by default for port 993
            )
            
            if not imap.connect():
                results["errors"].append(f"Inbox {inbox_id}: Failed to connect to IMAP")
                continue
                
            results["inboxes_checked"] += 1
            
            # Get recent emails (last 48 hours)
            emails = imap.fetch_recent_emails(folder="INBOX", since_hours=48, limit=100)
            
            # Build set of FROM addresses this campaign actually sent from
            _sender_emails = set()
            for _iid in inbox_ids:
                _ib = db.query(Inbox).filter(Inbox.id == _iid).first()
                if _ib:
                    _sender_emails.add(_ib.email.lower())
            
            for mail in emails:
                # Only check replies
                if not mail.get("is_reply"):
                    continue
                
                from_email = mail.get("from_email", "").lower()
                
                # Skip warmup/internal replies (from_email belongs to a system inbox)
                if from_email in all_system_inbox_emails:
                    continue
                
                cr = None
                
                # --- Primary: match via In-Reply-To → sent_message_id ---
                if has_stored_ids:
                    in_reply_to = (mail.get("in_reply_to") or "").strip()
                    refs = (mail.get("references") or "").strip()
                    candidate_ids = []
                    if in_reply_to:
                        candidate_ids.append(in_reply_to)
                    if refs:
                        candidate_ids.extend(refs.split())
                    for cid in candidate_ids:
                        if cid.strip() in message_id_to_cr:
                            cr = message_id_to_cr[cid.strip()]
                            break
                
                # --- Fallback: match by from_email + To header + subject ---
                if not cr and from_email in recipient_emails:
                    # Verify To: header matches this campaign's sender address
                    _to_raw = mail.get("to_full", mail.get("to", "")).lower()
                    _to_email = mail.get("to_email", "").lower()
                    if not _to_email or not any(addr in _to_raw for addr in _sender_emails):
                        continue
                    
                    # Verify reply subject matches this campaign's subject
                    _template_subjects = []
                    if campaign.template_ids:
                        from models.ses_template import SESEmailTemplate
                        _template_subjects = [t.subject_line for t in db.query(SESEmailTemplate.subject_line).filter(
                            SESEmailTemplate.id.in_(campaign.template_ids)).all() if t.subject_line]
                    if not _reply_matches_campaign(mail.get("subject", ""), campaign.subject or "", _template_subjects):
                        continue
                    
                    cr = recipient_emails[from_email]
                
                if not cr:
                    continue
                    
                    # Check if not already marked as replied
                    if cr.status != RecipientSendStatus.REPLIED:
                        cr.status = RecipientSendStatus.REPLIED
                        cr.replied_at = datetime.now(timezone.utc)
                        results["recipients_updated"] += 1
                        
                        # Update campaign stats
                        campaign.total_replies = (campaign.total_replies or 0) + 1
                    
                    results["replies_found"] += 1
                    results["replies"].append({
                        "from": from_email,
                        "subject": mail.get("subject", ""),
                        "date": mail.get("date", "")
                    })
            
            imap.disconnect()
        
        except Exception as e:
            results["errors"].append(f"Inbox {inbox_id}: {str(e)}")
    
    db.commit()
    return results


# =============================================================================
# Auto-Reply Templates for Campaign Responses
# =============================================================================

CAMPAIGN_AUTO_REPLY_TEMPLATES = [
    {
        "subject_prefix": "",
        "body": """Hi {first_name},

Thanks for getting back to me! I really appreciate you taking the time to reply.

{personalized_response}

Let me know if you have any other questions.

Best regards,
{sender_name}"""
    },
    {
        "subject_prefix": "",
        "body": """Hey {first_name},

Great to hear from you! Thanks for your response.

{personalized_response}

Feel free to reach out if you need anything else.

Cheers,
{sender_name}"""
    },
    {
        "subject_prefix": "",
        "body": """Hi {first_name},

Thank you for your reply!

{personalized_response}

I'm happy to help with anything else you might need.

Best,
{sender_name}"""
    }
]


class AutoReplyConfig(BaseModel):
    """Configuration for auto-reply behavior"""
    enabled: bool = True
    min_delay_minutes: int = 2  # Minimum delay before auto-reply
    max_delay_minutes: int = 15  # Maximum delay (15 minutes)
    custom_message: Optional[str] = None  # Optional custom response
    use_ai: bool = True  # Use AI for context-aware replies (default ON)
    ai_tone: str = "professional"  # AI tone: professional, friendly, casual
    ai_goal: str = "continue_conversation"  # AI goal: continue_conversation, schedule_call, provide_info, custom
    custom_goal: Optional[str] = None  # Free-text goal instructions when ai_goal='custom'


@router.post("/{campaign_id}/check-and-reply")
def check_and_schedule_auto_replies(
    campaign_id: int,
    config: AutoReplyConfig = None,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """
    Check for replies to a campaign and schedule auto-replies.
    
    This combines reply detection with automatic response scheduling.
    """
    import random
    from datetime import timedelta
    
    if config is None:
        config = AutoReplyConfig()
    
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    check_campaign_ownership(campaign, current_user)
    
    # Get inboxes used by this campaign
    inbox_ids = campaign.inbox_ids or []
    if not inbox_ids:
        recipient_inboxes = db.query(CampaignRecipient.inbox_id).filter(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.inbox_id.isnot(None)
        ).distinct().all()
        inbox_ids = [r[0] for r in recipient_inboxes]
    
    results = {
        "campaign_id": campaign_id,
        "inboxes_checked": 0,
        "replies_found": 0,
        "auto_replies_scheduled": 0,
        "errors": [],
        "scheduled_replies": []
    }
    
    # Get recipient emails for this campaign (only SENT status)
    campaign_recipients = db.query(CampaignRecipient, Recipient).join(
        Recipient, CampaignRecipient.recipient_id == Recipient.id
    ).filter(
        CampaignRecipient.campaign_id == campaign_id,
        CampaignRecipient.status == RecipientSendStatus.SENT
    ).all()
    
    recipient_map = {}
    for cr, recipient in campaign_recipients:
        recipient_map[recipient.email.lower()] = {"cr": cr, "recipient": recipient}
    
    if not recipient_map:
        return {"message": "No recipients to check replies for", **results}
    
    # Build set of all system inbox emails to filter out warmup/internal replies
    all_system_inbox_emails = set(
        ib.email.lower() for ib in db.query(Inbox).filter(Inbox.is_active == True).all()
    )
    
    # Build list of inboxes to check (including reply-to inboxes)
    inboxes_to_check = set()
    for inbox_id in inbox_ids:
        inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
        if not inbox:
            continue
        if inbox.reply_enabled and inbox.imap_host and inbox.imap_username:
            inboxes_to_check.add(inbox.id)
        # Also check reply-to inbox
        if inbox.reply_to_email:
            reply_to_inbox = db.query(Inbox).filter(
                Inbox.email == inbox.reply_to_email,
                Inbox.reply_enabled == True
            ).first()
            if reply_to_inbox and reply_to_inbox.imap_host and reply_to_inbox.imap_username:
                inboxes_to_check.add(reply_to_inbox.id)
    
    # Check each inbox
    for check_inbox_id in inboxes_to_check:
        inbox = db.query(Inbox).filter(Inbox.id == check_inbox_id).first()
        if not inbox or not inbox.reply_enabled or not inbox.imap_host or not inbox.imap_username:
            results["errors"].append(f"Inbox {check_inbox_id}: Replies not enabled or IMAP not configured")
            continue
        
        try:
            imap = IMAPService(
                inbox.imap_host,
                inbox.imap_port,
                inbox.imap_username,
                inbox.imap_password,
                True
            )
            
            if not imap.connect():
                results["errors"].append(f"Inbox {inbox_id}: Failed to connect")
                continue
            
            results["inboxes_checked"] += 1
            
            # Get recent emails (last 48 hours)
            emails = imap.fetch_recent_emails(folder="INBOX", since_hours=48, limit=100)
            
            # Build set of FROM addresses this campaign actually sent from
            _sender_emails = set()
            for _iid in inbox_ids:
                _ib = db.query(Inbox).filter(Inbox.id == _iid).first()
                if _ib:
                    _sender_emails.add(_ib.email.lower())
            
            for mail in emails:
                if not mail.get("is_reply"):
                    continue
                
                from_email = mail.get("from_email", "").lower()
                
                # Skip warmup/internal replies (from_email belongs to a system inbox)
                if from_email in all_system_inbox_emails:
                    continue
                
                if from_email not in recipient_map:
                    continue
                
                # Verify To: header matches this campaign's sender address
                _to_raw = mail.get("to_full", mail.get("to", "")).lower()
                _to_email = mail.get("to_email", "").lower()
                if not _to_email or not any(addr in _to_raw for addr in _sender_emails):
                    continue
                
                # Verify reply subject matches this campaign's subject (including template rotation subjects)
                mail_subject = mail.get("subject", "")
                _template_subjects = []
                if campaign.template_ids:
                    from models.ses_template import SESEmailTemplate
                    _template_subjects = [t.subject_line for t in db.query(SESEmailTemplate.subject_line).filter(
                        SESEmailTemplate.id.in_(campaign.template_ids)).all() if t.subject_line]
                if not _reply_matches_campaign(mail_subject, campaign.subject or "", _template_subjects):
                    continue
                
                data = recipient_map[from_email]
                cr = data["cr"]
                recipient = data["recipient"]
                
                # Get unique identifier for this specific email
                mail_date = mail.get("date", "")
                mail_message_id = mail.get("message_id", "")
                
                # Check if we already processed THIS specific email (GLOBAL dedup by IMAP Message-ID)
                existing_reply = None
                if mail_message_id:
                    existing_reply = db.query(CampaignAutoReply).filter(
                        CampaignAutoReply.original_message_id == mail_message_id,
                    ).first()
                
                if existing_reply:
                    continue  # Already scheduled for THIS specific email
                
                results["replies_found"] += 1
                
                # Mark as replied by setting replied_at (keep status as SENT to avoid enum issues)
                if not cr.replied_at:
                    cr.replied_at = datetime.now(timezone.utc)
                    campaign.total_replies = (campaign.total_replies or 0) + 1
                
                if not config.enabled:
                    continue
                
                # Schedule auto-reply with random delay
                delay_minutes = random.randint(config.min_delay_minutes, config.max_delay_minutes)
                scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
                
                # Get original subject
                original_subject = mail.get("subject", campaign.subject)
                if not original_subject.lower().startswith("re:"):
                    subject = f"Re: {original_subject}"
                else:
                    subject = original_subject
                
                # Generate reply content - AI or template
                first_name = recipient.first_name or from_email.split("@")[0]
                sender_name = inbox.email.split("@")[0].replace(".", " ").title()
                
                if config.use_ai:
                    # Use AI for context-aware reply
                    try:
                        from services.ai_reply_service import generate_ai_reply, ConversationHistory, ai_reply_generator
                        
                        if ai_reply_generator.is_available():
                            # Get conversation history
                            history_service = ConversationHistory(db)
                            conversation_history = history_service.get_thread_history(
                                campaign_id=campaign_id,
                                recipient_email=from_email
                            )
                            
                            # Generate AI reply
                            ai_result = generate_ai_reply(
                                incoming_email={
                                    "from_email": from_email,
                                    "subject": mail.get("subject", ""),
                                    "body": mail.get("body_text", "")
                                },
                                conversation_history=conversation_history,
                                sender_info={
                                    "name": sender_name,
                                    "email": inbox.email
                                },
                                campaign_context={
                                    "purpose": campaign.name,
                                    "product": config.custom_message or "our services",
                                    "call_to_action": config.ai_goal,
                                    "custom_goal": config.custom_goal or "",
                                    "original_email_subject": campaign.subject or "",
                                    "original_email_body": (campaign.body_text or campaign.body_html or "")[:1000],
                                },
                                tone=config.ai_tone,
                                goal=config.ai_goal
                            )
                            
                            body_text = ai_result["body_text"]
                            body_html = ai_result["body_html"]
                        else:
                            # AI not available, fall back to template
                            raise Exception("AI not available")
                            
                    except Exception as e:
                        logger.warning(f"AI reply failed, using template: {e}")
                        # Fall back to template
                        template = random.choice(CAMPAIGN_AUTO_REPLY_TEMPLATES)
                        personalized = config.custom_message or "I wanted to follow up on your message."
                        body_text = template["body"].format(
                            first_name=first_name,
                            sender_name=sender_name,
                            personalized_response=personalized
                        )
                        body_html = f"<p>{body_text.replace(chr(10), '</p><p>')}</p>"
                else:
                    # Use template-based reply
                    template = random.choice(CAMPAIGN_AUTO_REPLY_TEMPLATES)
                    personalized = config.custom_message or "I wanted to follow up on your message."
                    body_text = template["body"].format(
                        first_name=first_name,
                        sender_name=sender_name,
                        personalized_response=personalized
                    )
                    body_html = f"<p>{body_text.replace(chr(10), '</p><p>')}</p>"
                
                # Create auto-reply record
                _pcr_thread_id = None
                if cr.thread_id:
                    from services.thread_id import make_full_thread_id as _mk_ftid
                    _pcr_ar_count = db.query(CampaignAutoReply).filter(
                        CampaignAutoReply.campaign_id == campaign_id,
                        CampaignAutoReply.campaign_recipient_id == cr.id,
                    ).count()
                    _pcr_thread_id = _mk_ftid(cr.thread_id, _pcr_ar_count + 1)
                
                auto_reply = CampaignAutoReply(
                    campaign_id=campaign_id,
                    campaign_recipient_id=cr.id,
                    inbox_id=inbox.id,
                    to_email=from_email,
                    from_email=inbox.email,
                    subject=subject,
                    original_subject=campaign.subject,
                    body_text=body_text,
                    body_html=f"<p>{body_text.replace(chr(10), '</p><p>')}</p>",
                    original_reply_subject=mail.get("subject"),
                    original_reply_snippet=mail.get("body_text", "")[:500] if mail.get("body_text") else None,
                    original_message_id=mail_message_id if mail_message_id else None,
                    scheduled_at=scheduled_at,
                    status="pending",
                    thread_id=_pcr_thread_id,
                )
                
                db.add(auto_reply)
                results["auto_replies_scheduled"] += 1
                results["scheduled_replies"].append({
                    "to": from_email,
                    "scheduled_at": scheduled_at.isoformat(),
                    "delay_minutes": delay_minutes
                })
            
            imap.disconnect()
            
        except Exception as e:
            results["errors"].append(f"Inbox {inbox_id}: {str(e)}")
    
    db.commit()
    return results


def _check_inbox_imap(inbox_data: dict) -> dict:
    """
    Check a single inbox via IMAP - runs in a thread for parallelization.
    Uses fast header-only fetch for speed. Stores credentials for later body fetches.
    """
    result = {"inbox_id": inbox_data["inbox_id"], "emails": [], "error": None,
              "imap_creds": {k: inbox_data[k] for k in ("imap_host", "imap_port", "imap_username", "imap_password")}}
    try:
        imap = IMAPService(
            inbox_data["imap_host"], inbox_data["imap_port"],
            inbox_data["imap_username"], inbox_data["imap_password"], True
        )
        if not imap.connect(timeout=15):
            result["error"] = f"Failed to connect to IMAP for {inbox_data['email']}"
            return result
        emails = imap.fetch_reply_headers(folder="INBOX", since_hours=24, limit=500)
        result["emails"] = emails
        imap.disconnect()
    except Exception as e:
        result["error"] = str(e)
    return result


@router.post("/check-all-replies")
def check_all_campaign_replies(
    config: AutoReplyConfig = None,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Check replies for ALL recent campaigns at once with parallel IMAP checking."""
    if config is None:
        config = AutoReplyConfig()
    user_id = None if current_user.role == UserRole.ADMIN else current_user.id
    return _run_check_all_replies_logic(db, config, engine_filter=None, user_id=user_id)


def _run_check_all_replies_logic(
    db: Session,
    config,
    engine_filter: str = None,
    user_id: Optional[int] = None,
):
    """
    Core logic for checking IMAP replies across campaigns.
    
    Args:
        engine_filter: None = all engines, 'smtp' or 'ses' = scoped.
    """
    from datetime import timedelta
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import random
    
    recent_date = datetime.now(timezone.utc) - timedelta(days=7)
    campaign_query = db.query(Campaign).filter(
        Campaign.status.in_([CampaignStatus.RUNNING, CampaignStatus.COMPLETED, CampaignStatus.PAUSED]),
        Campaign.started_at >= recent_date
    )
    if engine_filter:
        campaign_query = campaign_query.filter(Campaign.engine_type.in_([engine_filter, 'mixed']))
    if user_id is not None:
        campaign_query = campaign_query.filter(Campaign.user_id == user_id)
    campaigns = campaign_query.all()
    
    total_results = {
        "campaigns_checked": 0,
        "total_inboxes_checked": 0,
        "total_replies_found": 0,
        "total_auto_replies_scheduled": 0,
        "errors": [],
        "per_campaign": []
    }
    
    # Phase 1: Gather all unique inboxes and campaign data (fast, DB-only)
    campaign_data_list = []
    all_inbox_ids = set()
    
    for campaign in campaigns:
        inbox_ids = campaign.inbox_ids or []
        if not inbox_ids:
            recipient_inboxes = db.query(CampaignRecipient.inbox_id).filter(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.inbox_id.isnot(None)
            ).distinct().all()
            inbox_ids = [r[0] for r in recipient_inboxes]
        
        campaign_recipients = db.query(CampaignRecipient, Recipient).join(
            Recipient, CampaignRecipient.recipient_id == Recipient.id
        ).filter(
            CampaignRecipient.campaign_id == campaign.id,
            CampaignRecipient.status == RecipientSendStatus.SENT
        ).all()
        
        recipient_map = {}
        for cr, recipient in campaign_recipients:
            recipient_map[recipient.email.lower()] = {"cr": cr, "recipient": recipient}
        
        if not recipient_map:
            continue
        
        inboxes_to_check = set()
        for inbox_id in inbox_ids:
            inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
            if not inbox:
                continue
            
            # Check if we need to filter by engine type
            if engine_filter:
                # Get the SMTP account to check provider_type
                smtp_account = db.query(SMTPAccount).filter(SMTPAccount.id == inbox.smtp_account_id).first()
                if not smtp_account:
                    continue
                
                # Determine if this inbox matches the engine filter
                provider = getattr(smtp_account, 'provider_type', 'smtp') or 'smtp'
                if engine_filter == 'smtp' and provider != 'smtp':
                    continue  # Skip non-SMTP inboxes when filtering for SMTP
                elif engine_filter == 'ses' and provider == 'smtp':
                    continue  # Skip SMTP inboxes when filtering for Resend.
            
            if inbox.reply_enabled and inbox.imap_host and inbox.imap_username:
                inboxes_to_check.add(inbox.id)
            if inbox.reply_to_email:
                reply_to_inbox = db.query(Inbox).filter(
                    Inbox.email == inbox.reply_to_email,
                    Inbox.reply_enabled == True
                ).first()
                if reply_to_inbox and reply_to_inbox.imap_host and reply_to_inbox.imap_username:
                    # Also check engine filter for reply-to inbox
                    if engine_filter and reply_to_inbox:
                        reply_smtp_account = db.query(SMTPAccount).filter(
                            SMTPAccount.id == reply_to_inbox.smtp_account_id
                        ).first()
                        if reply_smtp_account:
                            reply_provider = getattr(reply_smtp_account, 'provider_type', 'smtp') or 'smtp'
                            if engine_filter == 'smtp' and reply_provider != 'smtp':
                                continue  # Skip non-SMTP reply-to inboxes
                            elif engine_filter == 'ses' and reply_provider == 'smtp':
                                continue  # Skip SMTP reply-to inboxes
                    
                    inboxes_to_check.add(reply_to_inbox.id)
        
        all_inbox_ids.update(inboxes_to_check)
        campaign_data_list.append({
            "campaign": campaign,
            "recipient_map": recipient_map,
            "inboxes_to_check": inboxes_to_check,
        })
    
    # Phase 2: Fetch all inbox details and build IMAP tasks (deduplicated by credentials)
    inbox_cache = {}
    imap_tasks = []
    seen_credentials = {}  # "host:user" -> inbox_id (first one wins)
    credential_map = {}  # inbox_id -> credential_key (for result sharing)
    
    for inbox_id in all_inbox_ids:
        inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
        if not inbox or not inbox.reply_enabled or not inbox.imap_host or not inbox.imap_username:
            continue
        inbox_cache[inbox_id] = inbox
        
        cred_key = f"{inbox.imap_host}:{inbox.imap_username}".lower()
        credential_map[inbox_id] = cred_key
        
        if cred_key not in seen_credentials:
            seen_credentials[cred_key] = inbox_id
            imap_tasks.append({
                "inbox_id": inbox.id,
                "email": inbox.email,
                "imap_host": inbox.imap_host,
                "imap_port": inbox.imap_port,
                "imap_username": inbox.imap_username,
                "imap_password": inbox.imap_password,
                "cred_key": cred_key,
            })
    
    # Phase 3: Parallel IMAP fetching (deduplicated — each unique credential fetched once)
    imap_results = {}  # inbox_id -> list of emails
    cred_results = {}  # cred_key -> list of emails
    imap_creds_map = {}  # cred_key -> {imap_host, imap_port, imap_username, imap_password}
    if imap_tasks:
        with ThreadPoolExecutor(max_workers=min(len(imap_tasks), 10)) as executor:
            future_to_task = {
                executor.submit(_check_inbox_imap, task): task
                for task in imap_tasks
            }
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                inbox_id = task["inbox_id"]
                cred_key = task["cred_key"]
                try:
                    result = future.result()
                    if result["error"]:
                        total_results["errors"].append(f"Inbox {inbox_id}: {result['error']}")
                        cred_results[cred_key] = []
                    else:
                        cred_results[cred_key] = result["emails"]
                        imap_results[inbox_id] = result["emails"]
                        imap_creds_map[cred_key] = result.get("imap_creds", {})
                        total_results["total_inboxes_checked"] += 1
                except Exception as e:
                    total_results["errors"].append(f"Inbox {inbox_id}: {str(e)}")
                    cred_results[cred_key] = []
    
    # Share results to inboxes that use same credentials but weren't fetched
    for iid, ckey in credential_map.items():
        if iid not in imap_results and ckey in cred_results:
            imap_results[iid] = cred_results[ckey]
            total_results["total_inboxes_checked"] += 1
    
    # Build set of ALL system inbox emails to filter out warmup/internal replies
    all_system_inbox_emails = set()
    all_system_inboxes = db.query(Inbox).filter(Inbox.is_active == True).all()
    for _sys_inbox in all_system_inboxes:
        all_system_inbox_emails.add(_sys_inbox.email.lower())
    
    # Phase 4: Process fetched emails per campaign (fast, in-memory matching + AI)
    for cdata in campaign_data_list:
        campaign = cdata["campaign"]
        recipient_map = cdata["recipient_map"]
        campaign_replies = 0
        campaign_scheduled = 0
        
        # Build In-Reply-To lookup from stored sent_message_id
        _msg_id_lookup = {}  # sent_message_id -> {"cr": ..., "recipient": ...}
        _thread_id_lookup = {}  # thread_id (base) -> {"cr": ..., "recipient": ...}
        _has_stored_ids = False
        for _email_lower, _data in recipient_map.items():
            _cr = _data["cr"]
            if _cr.sent_message_id:
                _msg_id_lookup[_cr.sent_message_id] = _data
                _has_stored_ids = True
            if _cr.thread_id:
                _thread_id_lookup[_cr.thread_id] = _data
        
        # Build the set of FROM addresses this campaign actually sent from
        campaign_sender_emails = set()
        for iid in (campaign.inbox_ids or []):
            ib = inbox_cache.get(iid)
            if ib:
                campaign_sender_emails.add(ib.email.lower())
        
        try:
            for check_inbox_id in cdata["inboxes_to_check"]:
                inbox = inbox_cache.get(check_inbox_id)
                if not inbox:
                    continue
                emails = imap_results.get(check_inbox_id, [])
                
                for mail in emails:
                    if not mail.get("is_reply"):
                        continue
                    from_email = mail.get("from_email", "").lower()
                    
                    # Skip warmup/internal replies (from_email belongs to a system inbox)
                    if from_email in all_system_inbox_emails:
                        continue
                    
                    cr = None
                    recipient = None
                    
                    # --- Primary: In-Reply-To → sent_message_id lookup ---
                    if _has_stored_ids:
                        _in_reply_to = (mail.get("in_reply_to") or "").strip()
                        _refs = (mail.get("references") or "").strip()
                        _cand_ids = []
                        if _in_reply_to:
                            _cand_ids.append(_in_reply_to)
                        if _refs:
                            _cand_ids.extend(_refs.split())
                        for _cid in _cand_ids:
                            _matched = _msg_id_lookup.get(_cid.strip())
                            if _matched:
                                cr = _matched["cr"]
                                recipient = _matched["recipient"]
                                break
                    
                    # --- Thread ID matching (if In-Reply-To didn't match) ---
                    if not cr and _thread_id_lookup:
                        from services.thread_id import extract_thread_id_from_references, parse_thread_id
                        # Try X-Thread-ID header
                        _x_tid = mail.get("x_thread_id", "")
                        if _x_tid:
                            _parsed = parse_thread_id(_x_tid)
                            if _parsed and _parsed["base"] in _thread_id_lookup:
                                _tid_match = _thread_id_lookup[_parsed["base"]]
                                cr = _tid_match["cr"]
                                recipient = _tid_match["recipient"]
                        # Try extracting from References/In-Reply-To message IDs
                        if not cr:
                            _in_reply_to = (mail.get("in_reply_to") or "").strip()
                            _refs = (mail.get("references") or "").strip()
                            _combined = f"{_in_reply_to} {_refs}".strip()
                            _ext_tid = extract_thread_id_from_references(_combined)
                            if _ext_tid:
                                _parsed = parse_thread_id(_ext_tid)
                                if _parsed and _parsed["base"] in _thread_id_lookup:
                                    _tid_match = _thread_id_lookup[_parsed["base"]]
                                    cr = _tid_match["cr"]
                                    recipient = _tid_match["recipient"]
                    
                    # --- Fallback: subject + To + from matching ---
                    if not cr and from_email in recipient_map:
                        to_raw = mail.get("to", "").lower()
                        to_email = mail.get("to_email", "").lower()
                        if not to_email:
                            continue
                        if not any(addr in to_raw for addr in campaign_sender_emails):
                            continue
                        
                        mail_subject = mail.get("subject", "")
                        _template_subjects = []
                        if campaign.template_ids:
                            from models.ses_template import SESEmailTemplate
                            _template_subjects = [t.subject_line for t in db.query(SESEmailTemplate.subject_line).filter(
                                SESEmailTemplate.id.in_(campaign.template_ids)).all() if t.subject_line]
                        if not _reply_matches_campaign(mail_subject, campaign.subject or "", _template_subjects):
                            continue
                        
                        _data = recipient_map[from_email]
                        cr = _data["cr"]
                        recipient = _data["recipient"]
                    
                    if not cr:
                        continue
                    
                    mail_subject = mail.get("subject", "")
                    mail_message_id = mail.get("message_id", "")
                    
                    # Check for existing reply (GLOBAL dedup by IMAP Message-ID only)
                    existing_reply = None
                    if mail_message_id:
                        existing_reply = db.query(CampaignAutoReply).filter(
                            CampaignAutoReply.original_message_id == mail_message_id,
                        ).first()
                    if existing_reply:
                        continue
                    
                    campaign_replies += 1
                    # Always count each unique reply; mark first reply timestamp on recipient
                    campaign.total_replies = (campaign.total_replies or 0) + 1
                    if not cr.replied_at:
                        cr.replied_at = datetime.now(timezone.utc)
                    
                    # Fetch full email body for this matched reply (header-only fetch doesn't include body)
                    reply_body_text = ""
                    reply_body_html = ""
                    mail_uid = mail.get("uid", "")
                    if mail_uid:
                        _ckey = credential_map.get(check_inbox_id, "")
                        _creds = imap_creds_map.get(_ckey, {})
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
                    
                    delay_minutes = random.randint(config.min_delay_minutes, config.max_delay_minutes)
                    scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
                    
                    original_subject = mail.get("subject", campaign.subject)
                    subject = original_subject if original_subject.lower().startswith("re:") else f"Re: {original_subject}"
                    
                    first_name = recipient.first_name or from_email.split("@")[0]
                    sender_name = inbox.email.split("@")[0].replace(".", " ").title()
                    
                    # Generate reply content - AI or template (same as check-and-reply)
                    body_text = None
                    body_html = None
                    
                    if config.use_ai:
                        try:
                            from services.ai_reply_service import generate_ai_reply, ConversationHistory, ai_reply_generator
                            
                            if ai_reply_generator.is_available():
                                history_service = ConversationHistory(db)
                                conversation_history = history_service.get_thread_history(
                                    campaign_id=campaign.id,
                                    recipient_email=from_email
                                )
                                
                                ai_result = generate_ai_reply(
                                    incoming_email={
                                        "from_email": from_email,
                                        "subject": mail.get("subject", ""),
                                        "body": reply_body_text or mail.get("body_text", "")
                                    },
                                    conversation_history=conversation_history,
                                    sender_info={
                                        "name": sender_name,
                                        "email": inbox.email
                                    },
                                    campaign_context={
                                        "purpose": campaign.name,
                                        "product": config.custom_message or "our services",
                                        "call_to_action": config.ai_goal,
                                        "custom_goal": config.custom_goal or "",
                                        "original_email_subject": campaign.subject or "",
                                        "original_email_body": (campaign.body_text or campaign.body_html or "")[:1000],
                                    },
                                    tone=config.ai_tone,
                                    goal=config.ai_goal
                                )
                                
                                body_text = ai_result["body_text"]
                                body_html = ai_result["body_html"]
                            else:
                                raise Exception("AI not available")
                        except Exception as e:
                            logger.warning(f"AI reply failed for check-all, using template: {e}")
                            body_text = None
                    
                    if not body_text:
                        # Fallback to template
                        template = random.choice(CAMPAIGN_AUTO_REPLY_TEMPLATES)
                        personalized = config.custom_message or "I wanted to follow up on your message."
                        body_text = template["body"].format(
                            first_name=first_name,
                            sender_name=sender_name,
                            personalized_response=personalized
                        )
                        body_html = f"<p>{body_text.replace(chr(10), '</p><p>')}</p>"
                    
                    if not body_html:
                        body_html = f"<p>{body_text.replace(chr(10), '</p><p>')}</p>"
                    
                    # Determine thread_id for this reply
                    _reply_thread_id = None
                    if cr.thread_id:
                        from services.thread_id import make_full_thread_id as _make_full_tid
                        _existing_ar_count = db.query(CampaignAutoReply).filter(
                            CampaignAutoReply.campaign_id == campaign.id,
                            CampaignAutoReply.campaign_recipient_id == cr.id,
                        ).count()
                        _reply_thread_id = _make_full_tid(cr.thread_id, _existing_ar_count + 1)
                    
                    auto_reply = CampaignAutoReply(
                        campaign_id=campaign.id,
                        campaign_recipient_id=cr.id,
                        inbox_id=inbox.id,
                        to_email=from_email,
                        from_email=inbox.email,
                        subject=subject,
                        original_subject=campaign.subject,
                        body_text=body_text,
                        body_html=body_html,
                        original_reply_subject=mail_subject,
                        original_reply_snippet=reply_body_text[:500] if reply_body_text else (mail.get("body_text", "")[:500] if mail.get("body_text") else None),
                        original_message_id=mail_message_id if mail_message_id else None,
                        scheduled_at=scheduled_at,
                        status="pending",
                        thread_id=_reply_thread_id,
                    )
                    db.add(auto_reply)
                    campaign_scheduled += 1
            
            total_results["campaigns_checked"] += 1
            total_results["total_replies_found"] += campaign_replies
            total_results["total_auto_replies_scheduled"] += campaign_scheduled
            
            if campaign_replies > 0:
                total_results["per_campaign"].append({
                    "campaign_id": campaign.id,
                    "campaign_name": campaign.name,
                    "replies_found": campaign_replies,
                    "auto_replies_scheduled": campaign_scheduled,
                })
        except Exception as e:
            total_results["errors"].append(f"Campaign {campaign.id}: {str(e)}")
    
    db.commit()
    return total_results


@router.post("/process-auto-replies")
def process_pending_auto_replies(
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """
    Process all pending auto-replies that are due to be sent.
    
    Call this periodically (e.g., every 5 minutes) to send scheduled replies.
    Uses SELECT FOR UPDATE to prevent duplicate processing.
    """
    from sqlalchemy import text
    
    results = {
        "processed": 0,
        "queued": 0,
        "errors": [],
        "skipped": 0
    }
    
    # Use FOR UPDATE SKIP LOCKED to prevent race conditions
    # This ensures only one process can claim each reply
    pending_query = db.query(CampaignAutoReply).join(
        Campaign,
        CampaignAutoReply.campaign_id == Campaign.id,
    ).filter(
        CampaignAutoReply.status == "pending",
        CampaignAutoReply.scheduled_at <= datetime.now(timezone.utc)
    )
    if current_user.role != UserRole.ADMIN:
        pending_query = pending_query.filter(Campaign.user_id == current_user.id)
    pending = pending_query.with_for_update(skip_locked=True).all()
    
    # Cache fallback emails per user to avoid repeated queries
    _fallback_cache = {}

    def _get_fallback(user_id):
        if user_id not in _fallback_cache:
            fb = db.query(FallbackReplyEmail).filter(
                FallbackReplyEmail.user_id == user_id,
                FallbackReplyEmail.is_active == True
            ).first()
            _fallback_cache[user_id] = fb
        return _fallback_cache[user_id]

    for reply in pending:
        try:
            # Double-check status (in case of race)
            if reply.status != "pending":
                results["skipped"] += 1
                continue
            
            # Get inbox
            inbox = db.query(Inbox).filter(Inbox.id == reply.inbox_id).first()
            use_fallback = False
            fallback = None

            if not inbox:
                # No inbox found - try fallback
                # Determine user_id from campaign
                campaign = db.query(Campaign).filter(Campaign.id == reply.campaign_id).first()
                if campaign:
                    fallback = _get_fallback(campaign.user_id)
                if fallback:
                    use_fallback = True
                else:
                    reply.status = "failed"
                    reply.error_message = "Inbox not found and no fallback email configured"
                    db.commit()
                    results["errors"].append(f"Reply {reply.id}: Inbox not found")
                    continue
            else:
                # Check if inbox SMTP account exists and is usable
                smtp_account = db.query(SMTPAccount).filter(
                    SMTPAccount.id == inbox.smtp_account_id
                ).first()
                if not smtp_account or not smtp_account.is_active:
                    # SMTP missing or inactive - try fallback
                    if inbox.user_id:
                        fallback = _get_fallback(inbox.user_id)
                    if fallback:
                        use_fallback = True
                        logger.info(f"Reply {reply.id}: SMTP account issue, using fallback email {fallback.email}")
            
            # CRITICAL: Set status to "queued" BEFORE adding to queue
            # This prevents duplicate adds if function is called again
            reply.status = "queued"
            db.commit()  # Commit BEFORE queue add
            
            if use_fallback and fallback:
                # Use fallback email to send the reply
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
                if inbox:
                    email_data["smtp_account_id"] = inbox.smtp_account_id
                    email_data["inbox_id"] = inbox.id
            else:
                # Normal path - use inbox SMTP
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
            
            email_queue.add(email_data, Priority.HIGH)
            results["queued"] += 1
            
        except Exception as e:
            reply.status = "failed"
            reply.error_message = str(e)
            db.commit()
            results["errors"].append(f"Reply {reply.id}: {str(e)}")
        
        results["processed"] += 1
    
    return results


@router.get("/{campaign_id}/auto-replies")
def get_campaign_auto_replies(
    campaign_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Get all auto-replies for a campaign"""
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    check_campaign_ownership(campaign, current_user)

    replies = db.query(CampaignAutoReply).filter(
        CampaignAutoReply.campaign_id == campaign_id
    ).order_by(CampaignAutoReply.scheduled_at.desc()).all()
    
    return {
        "campaign_id": campaign_id,
        "total": len(replies),
        "replies": [
            {
                "id": r.id,
                "to_email": r.to_email,
                "subject": r.subject,
                "original_reply_subject": r.original_reply_subject,
                "original_reply_snippet": r.original_reply_snippet,
                "body_text": r.body_text,
                "status": r.status,
                "scheduled_at": r.scheduled_at.isoformat() if r.scheduled_at else None,
                "sent_at": r.sent_at.isoformat() if r.sent_at else None,
                "error_message": r.error_message
            }
            for r in replies
        ]
    }


@router.post("/auto-replies/{reply_id}/cancel")
def cancel_auto_reply(
    reply_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Cancel a pending auto-reply so it won't be sent."""
    reply_query = db.query(CampaignAutoReply).join(
        Campaign,
        CampaignAutoReply.campaign_id == Campaign.id,
    ).filter(CampaignAutoReply.id == reply_id)
    if current_user.role != UserRole.ADMIN:
        reply_query = reply_query.filter(Campaign.user_id == current_user.id)
    reply = reply_query.first()

    if not reply:
        raise HTTPException(status_code=404, detail="Auto-reply not found")

    if reply.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel reply with status '{reply.status}'. Only pending replies can be cancelled."
        )

    reply.status = "cancelled"
    reply.error_message = "Cancelled by user"
    db.commit()

    return {"success": True, "id": reply_id, "status": "cancelled"}


# =============================================================================
# Live Campaign Terminal
# =============================================================================

@router.get("/{campaign_id}/activity")
def get_campaign_activity(
    campaign_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get live activity log and current status for a campaign's terminal view."""
    import json as _json
    import time as _time

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    check_campaign_ownership(campaign, current_user)

    r = email_queue.redis  # Use the module-level singleton

    # Get activity log (last 100 entries)
    activity_key = f"campaign:{campaign_id}:activity"
    logs = r.lrange(activity_key, -100, -1) or []

    # Get current status
    status_key = f"campaign:{campaign_id}:status"
    status_raw = r.get(status_key)
    status = _json.loads(status_raw) if status_raw else {"state": "idle"}

    # Get cooldown info
    pacing_key = f"campaign:{campaign_id}:next_batch"
    next_batch_at = r.get(pacing_key)
    now = _time.time()
    if next_batch_at:
        remaining = float(next_batch_at) - now
        if remaining > 0:
            status["cooldown_remaining"] = int(remaining)
        else:
            status["cooldown_remaining"] = 0

    # Get the email schedule for live frontend countdowns
    schedule_key = f"campaign:{campaign_id}:schedule"
    schedule_raw = r.get(schedule_key)
    schedule = _json.loads(schedule_raw) if schedule_raw else None
    if schedule and schedule.get("emails"):
        for entry in schedule["emails"]:
            entry["seconds_until"] = max(0, round(entry["send_at"] - now))
        if schedule.get("cooldown_until"):
            schedule["cooldown_seconds"] = max(0, round(schedule["cooldown_until"] - now))

    # Surface worker diagnostics so terminals do not appear "silent".
    worker_status = {
        "alive": bool(r.exists("worker:heartbeat")),
        "timezone_alive": None,
        "timezone_status": None,
        "business_hours": None,
        "next_window_open": None,
    }
    timezone_worker_info = None
    if campaign.send_timezone:
        tz_key = f"worker:{campaign.send_timezone.replace('/', '_').lower()}:status"
        tz_raw = r.get(tz_key)
        worker_status["timezone_alive"] = bool(tz_raw)
        if tz_raw:
            try:
                timezone_worker_info = _json.loads(tz_raw)
            except Exception:
                timezone_worker_info = None
        if timezone_worker_info:
            worker_status["timezone_status"] = timezone_worker_info.get("status")
            worker_status["business_hours"] = timezone_worker_info.get("business_hours")
            worker_status["next_window_open"] = timezone_worker_info.get("next_window_open")

    if campaign.status == CampaignStatus.RUNNING:
        if not worker_status["alive"]:
            status["state"] = "idle"
            status["message"] = "Worker is offline. Start worker service to continue processing."
            warn_key = f"campaign:{campaign_id}:warn:worker_offline"
            if r.set(warn_key, "1", nx=True, ex=120):
                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                logs.append(f"[{ts}] ⚠️ Worker offline: campaign processing is paused")
        elif campaign.send_timezone and worker_status["timezone_alive"] is False:
            status["message"] = f"No active worker detected for {campaign.send_timezone} yet."
            warn_key = f"campaign:{campaign_id}:warn:timezone_worker_missing"
            if r.set(warn_key, "1", nx=True, ex=120):
                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                logs.append(f"[{ts}] ⏳ Waiting for {campaign.send_timezone} worker to pick up this campaign")
        elif timezone_worker_info and (
            str(timezone_worker_info.get("status") or "").lower() == "cooldown"
            or timezone_worker_info.get("business_hours") is False
        ):
            status["state"] = "cooldown"
            next_window_open = timezone_worker_info.get("next_window_open")
            local_time = timezone_worker_info.get("local_time")
            status["next_window_open"] = next_window_open
            if next_window_open:
                status["message"] = (
                    f"{campaign.send_timezone} worker is outside business hours "
                    f"({local_time or 'local time unavailable'}). Next window: {next_window_open}."
                )
            else:
                status["message"] = (
                    f"{campaign.send_timezone} worker is outside business hours "
                    f"({local_time or 'local time unavailable'})."
                )

            if next_window_open and "cooldown_remaining" not in status:
                try:
                    next_dt = datetime.fromisoformat(str(next_window_open).replace("Z", "+00:00"))
                    remaining = int((next_dt.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
                    status["cooldown_remaining"] = max(0, remaining)
                except Exception:
                    pass

            warn_key = f"campaign:{campaign_id}:warn:outside_business_hours"
            if r.set(warn_key, "1", nx=True, ex=180):
                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                if next_window_open:
                    logs.append(
                        f"[{ts}] ⏰ {campaign.send_timezone} worker outside business hours. Waiting until {next_window_open}"
                    )
                else:
                    logs.append(
                        f"[{ts}] ⏰ {campaign.send_timezone} worker outside business hours. Waiting for next window"
                    )

    if not logs and campaign.status in (CampaignStatus.RUNNING, CampaignStatus.PAUSED):
        pending_count = db.query(CampaignRecipient).filter(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.status == RecipientSendStatus.PENDING,
        ).count()
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        logs = [f"[{ts}] ℹ️ Campaign {campaign.status.value}. Pending recipients: {pending_count}"]

    return {
        "campaign_id": campaign_id,
        "campaign_name": campaign.name,
        "total_sent": campaign.total_sent,
        "total_failed": campaign.total_failed,
        "total_recipients": campaign.total_recipients,
        "status": status,
        "worker_status": worker_status,
        "schedule": schedule,
        "logs": logs,
        "server_time": now,
    }
