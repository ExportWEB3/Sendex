"""
Day 6: Monitoring API Endpoints
- View metrics and reports
- Check inbox health
- Manage alerts
- Auto-pause system
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional, List
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel

from database import get_db
from models.inbox import Inbox, InboxState
from models.monitoring import DailyMetrics, Alert, ReputationHistory, AlertSeverity, AlertType
from models.user import User, UserRole
from services.monitoring_service import (
    MetricsService, ReputationService, SafetyService, HealthReportService
)
from api.auth import require_auth

router = APIRouter(prefix="/api/monitoring", tags=["Monitoring"])


def _get_accessible_inbox(db: Session, inbox_id: int, current_user: User) -> Inbox:
    """Return an inbox visible to the current tenant without leaking its existence."""
    query = db.query(Inbox).filter(Inbox.id == inbox_id)
    if current_user.role != UserRole.ADMIN:
        query = query.filter(Inbox.user_id == current_user.id)
    inbox = query.first()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")
    return inbox


def _tenant_user_id(current_user: User) -> Optional[int]:
    return None if current_user.role == UserRole.ADMIN else current_user.id


# ==================== Health & Reports ====================

@router.get("/health")
def system_health(current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Get overall system health status"""
    
    safety_service = SafetyService(db)
    return safety_service.check_all_inboxes(user_id=_tenant_user_id(current_user))


@router.get("/health/{inbox_id}")
def inbox_health(inbox_id: int, current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Get health status for a specific inbox"""
    
    inbox = _get_accessible_inbox(db, inbox_id, current_user)
    safety_service = SafetyService(db)
    return safety_service.check_inbox_health(inbox)


@router.get("/report/daily")
def daily_report(current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Get daily health report for all inboxes"""
    
    report_service = HealthReportService(db)
    return report_service.generate_daily_report(user_id=_tenant_user_id(current_user))


@router.get("/report/inbox/{inbox_id}")
def inbox_report(inbox_id: int, current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Get detailed report for a specific inbox"""
    
    _get_accessible_inbox(db, inbox_id, current_user)
    report_service = HealthReportService(db)
    report = report_service.generate_inbox_report(inbox_id)
    
    if report.get("error"):
        raise HTTPException(status_code=404, detail=report["error"])
    
    return report


# ==================== Metrics ====================

@router.get("/metrics/{inbox_id}")
def get_inbox_metrics(
    inbox_id: int,
    days: int = Query(default=7, ge=1, le=90),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get metrics summary for an inbox"""
    
    _get_accessible_inbox(db, inbox_id, current_user)
    metrics_service = MetricsService(db)
    return metrics_service.get_inbox_metrics_summary(inbox_id, days)


@router.get("/metrics/{inbox_id}/daily")
def get_daily_metrics(
    inbox_id: int,
    date: Optional[str] = None,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get metrics for a specific day"""
    
    _get_accessible_inbox(db, inbox_id, current_user)
    target_date = datetime.now(timezone.utc)
    if date:
        try:
            target_date = datetime.fromisoformat(date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use ISO format: YYYY-MM-DD")
    
    metrics = db.query(DailyMetrics).filter(
        DailyMetrics.inbox_id == inbox_id,
        func.date(DailyMetrics.date) == target_date.date()
    ).first()
    
    if not metrics:
        return {
            "inbox_id": inbox_id,
            "date": target_date.date().isoformat(),
            "has_data": False,
            "message": "No metrics recorded for this date"
        }
    
    return {
        "inbox_id": inbox_id,
        "date": metrics.date.isoformat(),
        "has_data": True,
        "volume": {
            "emails_sent": metrics.emails_sent,
            "emails_received": metrics.emails_received,
            "warmup_sent": metrics.warmup_emails_sent,
            "campaign_sent": metrics.campaign_emails_sent
        },
        "delivery": {
            "delivered": metrics.delivered,
            "bounced": metrics.bounced,
            "soft_bounced": metrics.soft_bounced,
            "hard_bounced": metrics.hard_bounced,
            "delivery_rate": metrics.delivery_rate,
            "bounce_rate": metrics.bounce_rate
        },
        "engagement": {
            "opens": metrics.opens,
            "clicks": metrics.clicks,
            "replies": metrics.replies,
            "unsubscribes": metrics.unsubscribes,
            "open_rate": metrics.open_rate,
            "reply_rate": metrics.reply_rate
        },
        "reputation_score": metrics.reputation_score,
        "spam_complaints": metrics.spam_complaints
    }


@router.post("/metrics/{inbox_id}/calculate")
def calculate_metrics(inbox_id: int, current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Recalculate metrics from email records"""
    
    _get_accessible_inbox(db, inbox_id, current_user)
    metrics_service = MetricsService(db)
    metrics = metrics_service.calculate_metrics_from_emails(inbox_id)
    
    return {
        "calculated": True,
        "inbox_id": inbox_id,
        "date": metrics.date.isoformat(),
        "sent": metrics.emails_sent,
        "delivered": metrics.delivered,
        "bounced": metrics.bounced,
        "reputation": metrics.reputation_score
    }


# ==================== Reputation ====================

@router.get("/reputation/{inbox_id}")
def get_reputation(inbox_id: int, current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Get current reputation score for an inbox"""
    
    inbox = _get_accessible_inbox(db, inbox_id, current_user)
    reputation_service = ReputationService(db)
    score = reputation_service.calculate_reputation(inbox_id)
    
    status = "excellent" if score >= 90 else "good" if score >= 70 else "warning" if score >= 50 else "critical"
    
    return {
        "inbox_id": inbox_id,
        "email": inbox.email,
        "score": score,
        "status": status,
        "recommendation": _get_recommendation(score, status)
    }


@router.get("/reputation/{inbox_id}/trend")
def get_reputation_trend(
    inbox_id: int,
    days: int = Query(default=30, ge=7, le=90),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get reputation trend over time"""
    
    inbox = _get_accessible_inbox(db, inbox_id, current_user)
    reputation_service = ReputationService(db)
    return {
        "inbox_id": inbox_id,
        "email": inbox.email,
        "days": days,
        "trend": reputation_service.get_reputation_trend(inbox_id, days)
    }


@router.post("/reputation/{inbox_id}/update")
def update_reputation(inbox_id: int, current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Manually update/recalculate reputation"""
    
    _get_accessible_inbox(db, inbox_id, current_user)
    reputation_service = ReputationService(db)
    return reputation_service.update_reputation(inbox_id)


def _get_recommendation(score: float, status: str) -> str:
    """Get recommendation based on score"""
    
    if status == "excellent":
        return "Keep up the good work! Your inbox has excellent deliverability."
    elif status == "good":
        return "Your inbox is healthy. Continue warm-up and monitor engagement."
    elif status == "warning":
        return "Consider reducing send volume and improving content to boost engagement."
    else:
        return "URGENT: Pause sending immediately and investigate delivery issues."


# ==================== Alerts ====================

@router.get("/alerts")
def get_alerts(
    unread_only: bool = False,
    severity: Optional[str] = None,
    inbox_id: Optional[int] = None,
    limit: int = Query(default=50, le=200),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get alerts with optional filters"""
    
    query = db.query(Alert)
    if current_user.role != UserRole.ADMIN:
        query = query.join(Inbox, Alert.inbox_id == Inbox.id).filter(
            Inbox.user_id == current_user.id
        )
    
    if unread_only:
        query = query.filter(Alert.is_read == False)
    
    if severity:
        try:
            sev = AlertSeverity(severity)
            query = query.filter(Alert.severity == sev)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid severity. Use: {[s.value for s in AlertSeverity]}")
    
    if inbox_id:
        _get_accessible_inbox(db, inbox_id, current_user)
        query = query.filter(Alert.inbox_id == inbox_id)
    
    alerts = query.order_by(Alert.created_at.desc()).limit(limit).all()
    
    return {
        "total": len(alerts),
        "alerts": [
            {
                "id": a.id,
                "inbox_id": a.inbox_id,
                "type": a.alert_type.value,
                "severity": a.severity.value,
                "title": a.title,
                "message": a.message,
                "is_read": a.is_read,
                "is_resolved": a.is_resolved,
                "created_at": a.created_at.isoformat(),
                "action_taken": a.action_taken
            }
            for a in alerts
        ]
    }


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: int, current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Get a specific alert with full details"""
    
    query = db.query(Alert).filter(Alert.id == alert_id)
    if current_user.role != UserRole.ADMIN:
        query = query.join(Inbox, Alert.inbox_id == Inbox.id).filter(
            Inbox.user_id == current_user.id
        )
    alert = query.first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    
    # Mark as read
    if not alert.is_read:
        alert.is_read = True
        db.commit()
    
    return {
        "id": alert.id,
        "inbox_id": alert.inbox_id,
        "type": alert.alert_type.value,
        "severity": alert.severity.value,
        "title": alert.title,
        "message": alert.message,
        "data": alert.data,
        "is_read": alert.is_read,
        "is_resolved": alert.is_resolved,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
        "resolved_by": alert.resolved_by,
        "action_taken": alert.action_taken,
        "created_at": alert.created_at.isoformat()
    }


class ResolveAlertRequest(BaseModel):
    resolved_by: str = "admin"
    notes: Optional[str] = None


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, request: ResolveAlertRequest, current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Mark an alert as resolved"""
    
    query = db.query(Alert).filter(Alert.id == alert_id)
    if current_user.role != UserRole.ADMIN:
        query = query.join(Inbox, Alert.inbox_id == Inbox.id).filter(
            Inbox.user_id == current_user.id
        )
    alert = query.first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    
    alert.is_resolved = True
    alert.resolved_at = datetime.now(timezone.utc)
    alert.resolved_by = current_user.email
    if request.notes:
        alert.action_taken = request.notes
    
    db.commit()
    
    return {
        "resolved": True,
        "alert_id": alert_id,
        "resolved_by": alert.resolved_by
    }


@router.post("/alerts/read-all")
def mark_all_read(current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Mark all alerts as read"""
    
    query = db.query(Alert).filter(Alert.is_read == False)
    if current_user.role != UserRole.ADMIN:
        owned_inbox_ids = db.query(Inbox.id).filter(Inbox.user_id == current_user.id)
        query = query.filter(Alert.inbox_id.in_(owned_inbox_ids))
    count = query.update({"is_read": True}, synchronize_session=False)
    db.commit()
    
    return {
        "marked_read": count
    }


# ==================== Safety Actions ====================

@router.post("/safety/check")
def run_safety_check(current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Run safety check on all active inboxes and auto-pause if needed"""
    
    safety_service = SafetyService(db)
    return safety_service.check_all_inboxes(user_id=_tenant_user_id(current_user))


@router.post("/safety/check/{inbox_id}")
def run_inbox_safety_check(inbox_id: int, current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Run safety check on a specific inbox"""
    
    inbox = _get_accessible_inbox(db, inbox_id, current_user)
    safety_service = SafetyService(db)
    return safety_service.auto_pause_if_needed(inbox)


# ==================== Event Recording (for webhook handlers) ====================

class RecordEventRequest(BaseModel):
    event_type: str  # delivery, bounce, open, click, reply, spam_complaint
    bounce_type: Optional[str] = None  # soft, hard (for bounce events)


@router.post("/events/{inbox_id}/record")
def record_event(inbox_id: int, request: RecordEventRequest, current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Record a tracking event (for webhook handlers)"""
    
    _get_accessible_inbox(db, inbox_id, current_user)
    metrics_service = MetricsService(db)
    
    event_type = request.event_type.lower()
    
    if event_type == "delivery":
        metrics_service.record_delivery(inbox_id, delivered=True)
    elif event_type == "bounce":
        metrics_service.record_delivery(inbox_id, delivered=False, bounce_type=request.bounce_type)
    elif event_type in ["open", "click", "reply", "unsubscribe"]:
        metrics_service.record_engagement(inbox_id, event_type)
    elif event_type == "spam_complaint":
        metrics_service.record_spam_complaint(inbox_id)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown event type. Use: delivery, bounce, open, click, reply, unsubscribe, spam_complaint"
        )
    
    return {
        "recorded": True,
        "inbox_id": inbox_id,
        "event_type": event_type
    }
