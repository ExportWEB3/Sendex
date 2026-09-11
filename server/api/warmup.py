from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel

from database import get_db
from models.inbox import Inbox, InboxState, InboxGroup
from models.warmup import WarmupReply, WarmupPartner
from models.user import User, UserRole
from services.warmup_service import WarmupService
from services.warmup_email_generator import WarmupEmailGenerator
from services.reply_service import ReplyService, PartnerService
from api.auth import require_admin, require_auth

router = APIRouter(prefix="/api/warmup", tags=["Warm-up"])


def get_effective_user_id(current_user: User, requested_user_id: Optional[int] = None) -> int:
    """Get the effective user_id for queries."""
    if requested_user_id is not None and current_user.role == UserRole.ADMIN:
        return requested_user_id
    return current_user.id


def check_inbox_ownership(inbox: Inbox, current_user: User):
    """Check if user has access to inbox. Raises 403 if not."""
    if inbox.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")


# Schemas
class StartWarmupRequest(BaseModel):
    inbox_ids: List[int]


class PauseWarmupRequest(BaseModel):
    inbox_id: int
    reason: Optional[str] = None


# Endpoints
@router.get("/status")
def get_all_warmup_status(
    user_id: Optional[int] = Query(None, description="Admin only: view another user's warmup status"),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get warm-up status for all inboxes owned by current user"""
    effective_user_id = get_effective_user_id(current_user, user_id)
    
    service = WarmupService(db)
    inboxes = db.query(Inbox).filter(
        Inbox.is_active == True,
        Inbox.user_id == effective_user_id
    ).all()
    
    return {
        "total_inboxes": len(inboxes),
        "inboxes": [service.get_warmup_status(inbox) for inbox in inboxes]
    }


@router.get("/status/{inbox_id}")
def get_inbox_warmup_status(
    inbox_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get warm-up status for a specific inbox"""
    
    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    
    check_inbox_ownership(inbox, current_user)
    
    service = WarmupService(db)
    return service.get_warmup_status(inbox)


@router.post("/start/{inbox_id}")
def start_inbox_warmup(
    inbox_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Start warm-up for a specific inbox"""
    
    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    
    check_inbox_ownership(inbox, current_user)
    
    service = WarmupService(db)
    result = service.start_warmup(inbox)
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    
    return result


@router.post("/start-bulk")
def start_bulk_warmup(
    request: StartWarmupRequest,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Start warm-up for multiple inboxes"""
    
    service = WarmupService(db)
    results = []
    
    for inbox_id in request.inbox_ids:
        inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
        if inbox:
            # Check ownership
            if inbox.user_id != current_user.id and current_user.role != UserRole.ADMIN:
                results.append({"inbox_id": inbox_id, "success": False, "message": "Access denied"})
                continue
            result = service.start_warmup(inbox)
            results.append({"inbox_id": inbox_id, **result})
        else:
            results.append({"inbox_id": inbox_id, "success": False, "message": "Not found"})
    
    return {
        "results": results,
        "started": sum(1 for r in results if r.get("success"))
    }


@router.post("/pause/{inbox_id}")
def pause_inbox_warmup(inbox_id: int, reason: Optional[str] = None, current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Pause warm-up for an inbox"""
    
    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    
    check_inbox_ownership(inbox, current_user)
    
    service = WarmupService(db)
    result = service.pause_warmup(inbox, reason)
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    
    return result


@router.post("/resume/{inbox_id}")
def resume_inbox_warmup(inbox_id: int, current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Resume paused warm-up"""
    
    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    
    check_inbox_ownership(inbox, current_user)
    
    service = WarmupService(db)
    result = service.resume_warmup(inbox)
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    
    return result


@router.post("/stop/{inbox_id}")
def stop_inbox_warmup(inbox_id: int, current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Stop warm-up completely"""
    
    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    
    check_inbox_ownership(inbox, current_user)
    
    service = WarmupService(db)
    return service.stop_warmup(inbox)


@router.post("/queue-emails/{inbox_id}")
def queue_warmup_emails(inbox_id: int, count: Optional[int] = None, current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Manually queue warm-up emails for an inbox"""
    
    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    
    check_inbox_ownership(inbox, current_user)
    
    if inbox.state != InboxState.WARMING_UP:
        raise HTTPException(status_code=400, detail="Inbox is not in warming up state")
    
    service = WarmupService(db)
    
    # Get partner inboxes
    partners = service.get_partner_inboxes(inbox)
    if not partners:
        raise HTTPException(status_code=400, detail="No partner inboxes available")
    
    result = service.queue_warmup_emails(inbox, partners, count)
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    
    return result


@router.post("/run-daily")
def run_daily_warmup(current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Manually trigger daily warm-up routine (normally runs automatically)"""
    
    service = WarmupService(db)
    return service.run_daily_warmup()


@router.get("/calculate-volume/{day}")
def calculate_volume_for_day(day: int):
    """Calculate what volume should be for a specific warm-up day"""
    
    from services.warmup_service import WarmupService
    # Create temporary instance just for calculation
    volume = WarmupService(None).calculate_daily_volume(day)
    
    return {
        "day": day,
        "volume": volume,
        "description": f"Day {day} of warm-up should send approximately {volume} emails"
    }


@router.get("/volume-schedule")
def get_volume_schedule():
    """Get the warm-up volume schedule for finite or infinite mode."""
    
    from services.warmup_service import (
        WarmupService,
        WARMUP_DAYS,
        WARMUP_INFINITE,
        WARMUP_RAMP_DAYS,
        WARMUP_START_VOLUME,
        WARMUP_END_VOLUME,
    )
    
    service = WarmupService(None)
    schedule = []

    schedule_days = WARMUP_RAMP_DAYS if WARMUP_INFINITE else max(WARMUP_DAYS, 1)
    
    for day in range(1, schedule_days + 1):
        volume = service.calculate_daily_volume(day)
        schedule.append({"day": day, "volume": volume})
    
    return {
        "warmup_days": WARMUP_DAYS,
        "infinite": WARMUP_INFINITE,
        "ramp_days": WARMUP_RAMP_DAYS,
        "schedule_days": schedule_days,
        "start_volume": WARMUP_START_VOLUME,
        "end_volume": WARMUP_END_VOLUME,
        "schedule": schedule
    }


# Email generator preview endpoints
@router.get("/preview-email")
def preview_warmup_email(recipient_name: Optional[str] = None, sender_name: Optional[str] = None):
    """Preview a generated warm-up email"""
    
    email = WarmupEmailGenerator.generate_email(recipient_name, sender_name)
    return email


@router.get("/preview-reply")
def preview_warmup_reply(
    original_subject: str = "Quick question",
    reply_type: str = "acknowledgment",
    recipient_name: Optional[str] = None,
    sender_name: Optional[str] = None
):
    """Preview a generated warm-up reply"""
    
    reply = WarmupEmailGenerator.generate_reply(
        original_subject,
        recipient_name,
        sender_name,
        reply_type
    )
    return reply


@router.get("/subject-samples")
def get_subject_samples(count: int = 10):
    """Get sample subject lines"""
    
    import random
    subjects = random.sample(
        WarmupEmailGenerator.SUBJECT_TEMPLATES,
        min(count, len(WarmupEmailGenerator.SUBJECT_TEMPLATES))
    )
    return {"subjects": subjects}


# ==================== DAY 5: Partner & Reply System ====================

class AssignPartnersRequest(BaseModel):
    group: Optional[str] = None  # Specific group to assign partners within
    inbox_id: Optional[int] = None  # Specific inbox to assign partners to


@router.post("/partners/assign")
def assign_partners(request: AssignPartnersRequest = None, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Auto-assign warm-up partners. Can assign for a specific inbox, group, or all warming inboxes."""
    
    partner_service = PartnerService(db)
    
    # If specific inbox_id provided
    if request and request.inbox_id:
        inbox = db.query(Inbox).filter(Inbox.id == request.inbox_id).first()
        if not inbox:
            raise HTTPException(status_code=404, detail="Inbox not found")
        
        partners = partner_service.assign_partners(inbox)
        return {
            "success": True,
            "inbox_id": inbox.id,
            "email": inbox.email,
            "partners_assigned": len(partners)
        }
    
    # Get inboxes to assign partners to
    query = db.query(Inbox).filter(
        Inbox.is_active == True,
        Inbox.state.in_([InboxState.WARMING_UP, InboxState.WARMED_UP])
    )
    
    # Filter by group if specified
    if request and request.group:
        try:
            group = InboxGroup(request.group)
            query = query.filter(Inbox.warmup_group == group)
        except ValueError:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid group. Must be one of: {[g.value for g in InboxGroup]}"
            )
    
    inboxes = query.all()
    
    if not inboxes:
        return {
            "success": True,
            "message": "No inboxes found for partner assignment",
            "results": []
        }
    
    results = []
    total_assigned = 0
    
    for inbox in inboxes:
        partners = partner_service.assign_partners(inbox)
        total_assigned += len(partners)
        results.append({
            "inbox_id": inbox.id,
            "email": inbox.email,
            "partners_assigned": len(partners)
        })
    
    return {
        "success": True,
        "inboxes_processed": len(results),
        "total_partners_assigned": total_assigned,
        "results": results
    }


@router.get("/partners/{inbox_id}")
def get_inbox_partners(inbox_id: int, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Get all partners for an inbox"""
    
    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    
    # Get partners where this inbox is the primary
    partners = db.query(WarmupPartner).filter(
        WarmupPartner.inbox_id == inbox_id,
        WarmupPartner.is_active == True
    ).all()
    
    partner_list = []
    for p in partners:
        partner_inbox = db.query(Inbox).filter(Inbox.id == p.partner_inbox_id).first()
        partner_list.append({
            "partner_id": p.id,
            "partner_inbox_id": p.partner_inbox_id,
            "partner_email": partner_inbox.email if partner_inbox else None,
            "last_interaction": p.last_interaction.isoformat() if p.last_interaction else None,
            "cooldown_until": p.cooldown_until.isoformat() if p.cooldown_until else None,
            "interaction_count": p.interaction_count,
            "rotation_day": p.rotation_day,
            "is_active": p.is_active,
            "in_cooldown": p.cooldown_until and p.cooldown_until > datetime.now(timezone.utc)
        })
    
    return {
        "inbox_id": inbox_id,
        "inbox_email": inbox.email,
        "partner_count": len(partner_list),
        "partners": partner_list
    }


@router.post("/partners/rotate")
def rotate_partners(current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Check all inboxes and rotate partners if they've been assigned for 4+ days"""
    
    partner_service = PartnerService(db)
    result = partner_service.check_and_rotate_all()
    return result


@router.post("/partners/rotate/{inbox_id}")
def rotate_inbox_partners(inbox_id: int, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Force rotate partners for a specific inbox"""
    
    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    
    partner_service = PartnerService(db)
    result = partner_service.rotate_partners(inbox)
    return {
        "inbox_id": inbox_id,
        "email": inbox.email,
        **result
    }


@router.get("/partners/available/{inbox_id}")
def get_available_partner(inbox_id: int, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Get next available partner for sending (not in cooldown)"""
    
    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    
    partner_service = PartnerService(db)
    partner = partner_service.get_available_partner(inbox)
    
    if not partner:
        return {
            "inbox_id": inbox_id,
            "available": False,
            "message": "No partners available (all in cooldown or no partners assigned)"
        }
    
    partner_inbox = db.query(Inbox).filter(Inbox.id == partner.partner_inbox_id).first()
    
    return {
        "inbox_id": inbox_id,
        "available": True,
        "partner": {
            "partner_id": partner.id,
            "partner_inbox_id": partner.partner_inbox_id,
            "partner_email": partner_inbox.email if partner_inbox else None,
            "interaction_count": partner.interaction_count
        }
    }


# Reply scheduling endpoints
class ScheduleReplyRequest(BaseModel):
    original_message_id: str
    original_subject: str
    from_inbox_id: int
    to_inbox_id: int
    thread_id: Optional[int] = None


@router.post("/replies/schedule")
def schedule_reply(request: ScheduleReplyRequest, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Manually schedule a warm-up reply"""
    
    # Verify both inboxes exist
    from_inbox = db.query(Inbox).filter(Inbox.id == request.from_inbox_id).first()
    to_inbox = db.query(Inbox).filter(Inbox.id == request.to_inbox_id).first()
    
    if not from_inbox:
        raise HTTPException(status_code=404, detail="From inbox not found")
    if not to_inbox:
        raise HTTPException(status_code=404, detail="To inbox not found")
    
    reply_service = ReplyService(db)
    reply = reply_service.schedule_reply(
        original_message_id=request.original_message_id,
        original_subject=request.original_subject,
        from_inbox=from_inbox,
        to_inbox=to_inbox,
        thread_id=request.thread_id
    )
    
    if not reply:
        return {
            "scheduled": False,
            "message": "Reply not scheduled (70% probability check failed, which is normal)"
        }
    
    return {
        "scheduled": True,
        "reply_id": reply.id,
        "scheduled_at": reply.scheduled_at.isoformat(),
        "reply_type": reply.reply_type,
        "from_email": from_inbox.email,
        "to_email": to_inbox.email
    }


@router.get("/replies/pending")
def get_pending_replies(current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Get all pending replies that haven't been sent yet"""
    
    from datetime import datetime, timezone
    
    pending = db.query(WarmupReply).filter(
        WarmupReply.sent_at == None
    ).order_by(WarmupReply.scheduled_at).all()
    
    now = datetime.now(timezone.utc)
    
    replies = []
    for r in pending:
        from_inbox = db.query(Inbox).filter(Inbox.id == r.from_inbox_id).first()
        to_inbox = db.query(Inbox).filter(Inbox.id == r.to_inbox_id).first()
        
        replies.append({
            "reply_id": r.id,
            "thread_id": r.thread_id,
            "from_email": from_inbox.email if from_inbox else None,
            "to_email": to_inbox.email if to_inbox else None,
            "original_subject": r.original_subject,
            "reply_type": r.reply_type,
            "scheduled_at": r.scheduled_at.isoformat(),
            "ready_to_send": r.scheduled_at <= now
        })
    
    ready_count = sum(1 for r in replies if r["ready_to_send"])
    
    return {
        "total_pending": len(replies),
        "ready_to_send": ready_count,
        "replies": replies
    }


@router.post("/replies/process")
def process_pending_replies(current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Process and send all pending replies that are due"""
    
    reply_service = ReplyService(db)
    result = reply_service.process_pending_replies()
    return result


@router.post("/inbox/check/{inbox_id}")
def check_inbox_for_warmup(inbox_id: int, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Check an inbox for warm-up emails and schedule replies"""
    
    inbox = db.query(Inbox).filter(Inbox.id == inbox_id).first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    
    reply_service = ReplyService(db)
    result = reply_service.check_inbox_and_schedule_replies(inbox)
    return result


@router.post("/inbox/check-all")
def check_all_inboxes(current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Check all warming inboxes for warm-up emails"""
    
    # Get all inboxes that are warming up
    inboxes = db.query(Inbox).filter(
        Inbox.state == InboxState.WARMING_UP
    ).all()
    
    if not inboxes:
        return {
            "success": True,
            "message": "No inboxes currently warming up",
            "checked": 0
        }
    
    reply_service = ReplyService(db)
    results = []
    total_scheduled = 0
    
    for inbox in inboxes:
        result = reply_service.check_inbox_and_schedule_replies(inbox)
        if result.get("scheduled", 0) > 0:
            total_scheduled += result["scheduled"]
        results.append({
            "inbox_id": inbox.id,
            "email": inbox.email,
            **result
        })
    
    return {
        "success": True,
        "checked": len(inboxes),
        "total_replies_scheduled": total_scheduled,
        "results": results
    }


# Add missing import at function level
from datetime import datetime, timezone
