"""
Reply Dashboard API — engine-scoped, paginated endpoints.

/replies/{engine}/campaigns   — paginated campaign list with reply counts
/replies/{engine}/campaign/{id} — auto-reply details for one campaign (lazy load)
/replies/{engine}/check       — IMAP check scoped to engine type
/replies/process              — process pending auto-replies (shared)
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, case

from database import get_db
from models.campaign import Campaign, CampaignStatus, CampaignRecipient, RecipientSendStatus
from models.recipient import Recipient
from models.inbox import Inbox
from models.smtp_account import SMTPAccount
from models.user import User, UserRole
from models.warmup import CampaignAutoReply
from api.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/replies", tags=["replies"])


def _normalize_engine(engine: str) -> tuple[str, str]:
    """Return the public engine name and its legacy persisted value."""
    normalized = engine.strip().lower()
    if normalized == "smtp":
        return "smtp", "smtp"
    if normalized in ("resend", "ses"):
        return "resend", "ses"
    raise HTTPException(400, "engine must be 'smtp' or 'resend'")


# ─────────────────────────────────────────────────────
#  Paginated campaign list with reply stats
# ─────────────────────────────────────────────────────

@router.get("/{engine}/campaigns")
def get_reply_campaigns(
    engine: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=50),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """
    Paginated list of campaigns for a given engine type, with reply stats.
    Only includes campaigns that have sent at least 1 email.
    """
    public_engine, stored_engine = _normalize_engine(engine)

    recent_date = datetime.now(timezone.utc) - timedelta(days=60)

    base_q = db.query(Campaign).filter(
        Campaign.engine_type.in_([stored_engine, 'mixed']),
        Campaign.total_sent > 0,
        Campaign.status.in_([
            CampaignStatus.RUNNING,
            CampaignStatus.COMPLETED,
            CampaignStatus.PAUSED,
            CampaignStatus.CANCELLED,
        ]),
        Campaign.started_at >= recent_date,
    )
    if current_user.role != UserRole.ADMIN:
        base_q = base_q.filter(Campaign.user_id == current_user.id)

    total = base_q.count()
    campaigns = (
        base_q.order_by(Campaign.started_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    # Batch-fetch reply counts for these campaigns from CampaignAutoReply
    campaign_ids = [c.id for c in campaigns]
    reply_counts = {}
    pending_counts = {}
    if campaign_ids:
        stats = (
            db.query(
                CampaignAutoReply.campaign_id,
                func.count(CampaignAutoReply.id).label("total"),
                func.sum(case((CampaignAutoReply.status == "pending", 1), else_=0)).label("pending"),
                func.sum(case((CampaignAutoReply.status.in_(["sent", "queued"]), 1), else_=0)).label("sent"),
            )
            .filter(CampaignAutoReply.campaign_id.in_(campaign_ids))
            .group_by(CampaignAutoReply.campaign_id)
            .all()
        )
        for row in stats:
            reply_counts[row.campaign_id] = {
                "auto_total": row.total or 0,
                "auto_pending": row.pending or 0,
                "auto_sent": row.sent or 0,
            }

    items = []
    for c in campaigns:
        rc = reply_counts.get(c.id, {"auto_total": 0, "auto_pending": 0, "auto_sent": 0})
        items.append({
            "id": c.id,
            "name": c.name,
            "subject": c.subject,
            "status": c.status.value,
            "engine_type": "resend" if c.engine_type == "ses" else c.engine_type,
            "total_sent": c.total_sent,
            "total_replies": c.total_replies or 0,
            "reply_rate": round(((c.total_replies or 0) / c.total_sent * 100), 1) if c.total_sent else 0,
            "started_at": c.started_at.isoformat() if c.started_at else None,
            "auto_replies_total": rc["auto_total"],
            "auto_replies_pending": rc["auto_pending"],
            "auto_replies_sent": rc["auto_sent"],
        })

    return {
        "engine": public_engine,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": max(1, -(-total // per_page)),  # ceil div
        "campaigns": items,
    }


# ─────────────────────────────────────────────────────
#  Campaign auto-reply details (lazy load on expand)
# ─────────────────────────────────────────────────────

@router.get("/{engine}/campaign/{campaign_id}")
def get_campaign_reply_details(
    engine: str,
    campaign_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Fetch auto-reply threads for a single campaign. Lazy-loaded by the frontend."""
    _, stored_engine = _normalize_engine(engine)

    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.engine_type.in_([stored_engine, 'mixed']),
    )
    if current_user.role != UserRole.ADMIN:
        campaign = campaign.filter(Campaign.user_id == current_user.id)
    campaign = campaign.first()
    if not campaign:
        raise HTTPException(404, "Campaign not found or engine mismatch")

    replies = (
        db.query(CampaignAutoReply)
        .filter(CampaignAutoReply.campaign_id == campaign_id)
        .order_by(CampaignAutoReply.scheduled_at.desc())
        .all()
    )

    return {
        "campaign_id": campaign_id,
        "campaign_name": campaign.name,
        "total": len(replies),
        "replies": [
            {
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
            }
            for r in replies
        ],
    }


# ─────────────────────────────────────────────────────
#  Engine-scoped IMAP check
# ─────────────────────────────────────────────────────

from pydantic import BaseModel

class ReplyCheckConfig(BaseModel):
    enabled: bool = True
    min_delay_minutes: int = 2
    max_delay_minutes: int = 15
    use_ai: bool = True
    ai_tone: str = "professional"
    ai_goal: str = "continue_conversation"
    custom_goal: Optional[str] = None
    custom_message: Optional[str] = None


@router.post("/{engine}/check")
def check_engine_replies(
    engine: str,
    config: ReplyCheckConfig = None,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """
    Check IMAP for new replies — scoped to the given engine type.
    
    SMTP connects to each inbox's own IMAP; Resend uses the shared reply inbox.
    """
    _, stored_engine = _normalize_engine(engine)
    if config is None:
        config = ReplyCheckConfig()

    # Delegate to the existing check-all-replies logic, but filtered by engine.
    # We import the heavy function from the campaign API module.
    from api.campaign import _run_check_all_replies_logic

    user_id = None if current_user.role == UserRole.ADMIN else current_user.id
    result = _run_check_all_replies_logic(
        db,
        config,
        engine_filter=stored_engine,
        user_id=user_id,
    )
    return result


# ─────────────────────────────────────────────────────
#  Aggregate stats for a single engine
# ─────────────────────────────────────────────────────

@router.get("/{engine}/stats")
def get_engine_reply_stats(
    engine: str,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Quick aggregate stats for an engine's reply dashboard header."""
    public_engine, stored_engine = _normalize_engine(engine)

    recent_date = datetime.now(timezone.utc) - timedelta(days=60)

    campaign_query = db.query(Campaign).filter(
        Campaign.engine_type.in_([stored_engine, 'mixed']),
        Campaign.total_sent > 0,
        Campaign.started_at >= recent_date,
    )
    if current_user.role != UserRole.ADMIN:
        campaign_query = campaign_query.filter(Campaign.user_id == current_user.id)
    campaigns = campaign_query.all()

    total_sent = sum(c.total_sent for c in campaigns)
    total_replies = sum(c.total_replies or 0 for c in campaigns)

    campaign_ids = [c.id for c in campaigns]
    auto_pending = 0
    auto_sent_count = 0
    if campaign_ids:
        row = db.query(
            func.sum(case((CampaignAutoReply.status == "pending", 1), else_=0)),
            func.sum(case((CampaignAutoReply.status.in_(["sent", "queued"]), 1), else_=0)),
        ).filter(CampaignAutoReply.campaign_id.in_(campaign_ids)).first()
        if row:
            auto_pending = row[0] or 0
            auto_sent_count = row[1] or 0

    campaigns_with_replies = sum(1 for c in campaigns if (c.total_replies or 0) > 0)

    return {
        "engine": public_engine,
        "total_campaigns": len(campaigns),
        "total_sent": total_sent,
        "total_replies": total_replies,
        "reply_rate": round((total_replies / total_sent * 100), 1) if total_sent else 0,
        "auto_replies_pending": auto_pending,
        "auto_replies_sent": auto_sent_count,
        "campaigns_with_replies": campaigns_with_replies,
    }
