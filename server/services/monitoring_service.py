"""
Day 6: Monitoring Service
- Track metrics per inbox
- Calculate reputation scores
- Generate daily reports
- Auto-pause on reputation drop
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from models.inbox import Inbox, InboxState
from models.email import Email, EmailStatus, EmailType
from models.monitoring import (
    DailyMetrics, Alert, ReputationHistory, HealthCheck,
    AlertType, AlertSeverity
)

logger = logging.getLogger(__name__)


# Thresholds for auto-pause and alerts
THRESHOLDS = {
    "bounce_rate_warning": 5.0,      # 5% bounce rate triggers warning
    "bounce_rate_critical": 10.0,    # 10% triggers pause
    "delivery_rate_warning": 90.0,   # Below 90% is warning
    "delivery_rate_critical": 80.0,  # Below 80% triggers pause
    "spam_complaint_critical": 0.1,  # 0.1% spam complaint triggers pause
    "reputation_warning": 70,        # Score below 70 is warning
    "reputation_critical": 50,       # Score below 50 triggers pause
}


class MetricsService:
    """Service for tracking and calculating metrics"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_or_create_daily_metrics(self, inbox_id: int, date: datetime = None) -> DailyMetrics:
        """Get or create daily metrics for an inbox"""
        
        date = date or datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        
        metrics = self.db.query(DailyMetrics).filter(
            DailyMetrics.inbox_id == inbox_id,
            func.date(DailyMetrics.date) == date.date()
        ).first()
        
        if not metrics:
            metrics = DailyMetrics(
                inbox_id=inbox_id,
                date=date
            )
            self.db.add(metrics)
            self.db.commit()
            self.db.refresh(metrics)
        
        return metrics
    
    def record_email_sent(self, inbox_id: int, email_type: EmailType = EmailType.CAMPAIGN):
        """Record an email was sent"""
        
        metrics = self.get_or_create_daily_metrics(inbox_id)
        metrics.emails_sent += 1
        
        if email_type == EmailType.WARMUP:
            metrics.warmup_emails_sent += 1
        else:
            metrics.campaign_emails_sent += 1
        
        self.db.commit()
    
    def record_email_received(self, inbox_id: int, is_warmup: bool = False):
        """Record an email was received"""
        
        metrics = self.get_or_create_daily_metrics(inbox_id)
        metrics.emails_received += 1
        
        if is_warmup:
            metrics.warmup_emails_received += 1
        
        self.db.commit()
    
    def record_delivery(self, inbox_id: int, delivered: bool = True, bounce_type: str = None):
        """Record delivery status"""
        
        metrics = self.get_or_create_daily_metrics(inbox_id)
        
        if delivered:
            metrics.delivered += 1
        else:
            metrics.bounced += 1
            if bounce_type == "soft":
                metrics.soft_bounced += 1
            elif bounce_type == "hard":
                metrics.hard_bounced += 1
        
        # Recalculate rates
        self._recalculate_rates(metrics)
        self.db.commit()
    
    def record_engagement(self, inbox_id: int, engagement_type: str):
        """Record engagement (open, click, reply, unsubscribe)"""
        
        metrics = self.get_or_create_daily_metrics(inbox_id)
        
        if engagement_type == "open":
            metrics.opens += 1
        elif engagement_type == "click":
            metrics.clicks += 1
        elif engagement_type == "reply":
            metrics.replies += 1
        elif engagement_type == "unsubscribe":
            metrics.unsubscribes += 1
        
        self._recalculate_rates(metrics)
        self.db.commit()
    
    def record_spam_complaint(self, inbox_id: int):
        """Record a spam complaint - serious event"""
        
        metrics = self.get_or_create_daily_metrics(inbox_id)
        metrics.spam_complaints += 1
        
        self._recalculate_rates(metrics)
        self.db.commit()
        
        # Check if we need to create an alert
        if metrics.emails_sent > 0:
            complaint_rate = (metrics.spam_complaints / metrics.emails_sent) * 100
            if complaint_rate >= THRESHOLDS["spam_complaint_critical"]:
                self._create_alert(
                    inbox_id=inbox_id,
                    alert_type=AlertType.SPAM_COMPLAINT,
                    severity=AlertSeverity.CRITICAL,
                    title="Spam Complaint Detected",
                    message=f"Spam complaint rate is {complaint_rate:.2f}%. Inbox should be paused.",
                    data={"complaint_rate": complaint_rate, "complaints": metrics.spam_complaints}
                )
    
    def _recalculate_rates(self, metrics: DailyMetrics):
        """Recalculate rate fields"""
        
        if metrics.emails_sent > 0:
            metrics.delivery_rate = (metrics.delivered / metrics.emails_sent) * 100
            metrics.bounce_rate = (metrics.bounced / metrics.emails_sent) * 100
            metrics.open_rate = (metrics.opens / metrics.emails_sent) * 100
            metrics.reply_rate = (metrics.replies / metrics.emails_sent) * 100
    
    def calculate_metrics_from_emails(self, inbox_id: int, date: datetime = None) -> DailyMetrics:
        """Calculate metrics from email records for a day"""
        
        date = date or datetime.now(timezone.utc)
        start_of_day = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)
        
        # Get inbox
        inbox = self.db.query(Inbox).filter(Inbox.id == inbox_id).first()
        if not inbox:
            return None
        
        metrics = self.get_or_create_daily_metrics(inbox_id, start_of_day)
        
        # Count emails sent from this inbox's SMTP
        sent_emails = self.db.query(Email).filter(
            Email.smtp_account_id == inbox.smtp_account_id,
            Email.created_at >= start_of_day,
            Email.created_at < end_of_day
        ).all()
        
        metrics.emails_sent = len(sent_emails)
        metrics.warmup_emails_sent = sum(1 for e in sent_emails if e.email_type == EmailType.WARMUP)
        metrics.campaign_emails_sent = sum(1 for e in sent_emails if e.email_type == EmailType.CAMPAIGN)
        
        # Count by status
        metrics.delivered = sum(1 for e in sent_emails if e.status == EmailStatus.DELIVERED)
        metrics.bounced = sum(1 for e in sent_emails if e.status == EmailStatus.BOUNCED)
        metrics.opens = sum(1 for e in sent_emails if e.opened_at is not None)
        metrics.replies = sum(1 for e in sent_emails if e.replied_at is not None)
        
        self._recalculate_rates(metrics)
        self.db.commit()
        
        return metrics
    
    def get_inbox_metrics_summary(self, inbox_id: int, days: int = 7) -> Dict[str, Any]:
        """Get metrics summary for an inbox over the last N days"""
        
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        
        metrics = self.db.query(DailyMetrics).filter(
            DailyMetrics.inbox_id == inbox_id,
            DailyMetrics.date >= start_date
        ).all()
        
        if not metrics:
            return {
                "inbox_id": inbox_id,
                "days": days,
                "has_data": False
            }
        
        # Aggregate
        total_sent = sum(m.emails_sent for m in metrics)
        total_delivered = sum(m.delivered for m in metrics)
        total_bounced = sum(m.bounced for m in metrics)
        total_opens = sum(m.opens for m in metrics)
        total_replies = sum(m.replies for m in metrics)
        total_complaints = sum(m.spam_complaints for m in metrics)
        
        return {
            "inbox_id": inbox_id,
            "days": days,
            "has_data": True,
            "totals": {
                "sent": total_sent,
                "delivered": total_delivered,
                "bounced": total_bounced,
                "opens": total_opens,
                "replies": total_replies,
                "spam_complaints": total_complaints
            },
            "rates": {
                "delivery_rate": (total_delivered / total_sent * 100) if total_sent > 0 else 100,
                "bounce_rate": (total_bounced / total_sent * 100) if total_sent > 0 else 0,
                "open_rate": (total_opens / total_sent * 100) if total_sent > 0 else 0,
                "reply_rate": (total_replies / total_sent * 100) if total_sent > 0 else 0
            },
            "daily_breakdown": [
                {
                    "date": m.date.isoformat(),
                    "sent": m.emails_sent,
                    "delivered": m.delivered,
                    "bounced": m.bounced,
                    "reputation": m.reputation_score
                }
                for m in sorted(metrics, key=lambda x: x.date)
            ]
        }
    
    def _create_alert(self, inbox_id: int, alert_type: AlertType, severity: AlertSeverity,
                      title: str, message: str, data: dict = None):
        """Create an alert"""
        
        alert = Alert(
            inbox_id=inbox_id,
            alert_type=alert_type,
            severity=severity,
            title=title,
            message=message,
            data=data or {}
        )
        self.db.add(alert)
        self.db.commit()
        
        logger.warning(f"Alert created: [{severity.value}] {title}")
        
        return alert


class ReputationService:
    """Service for calculating and tracking reputation"""
    
    def __init__(self, db: Session):
        self.db = db
        self.metrics_service = MetricsService(db)
    
    def calculate_reputation(self, inbox_id: int, days: int = 7) -> float:
        """
        Calculate reputation score (0-100) based on:
        - Delivery rate: 40 points max
        - Engagement (opens, replies): 30 points max
        - Complaints/bounces (negative): 30 points deducted max
        """
        
        summary = self.metrics_service.get_inbox_metrics_summary(inbox_id, days)
        
        if not summary.get("has_data"):
            return 100.0  # New inbox starts at 100
        
        rates = summary["rates"]
        totals = summary["totals"]
        
        # Delivery component (0-40 points)
        delivery_rate = rates["delivery_rate"]
        if delivery_rate >= 98:
            delivery_score = 40
        elif delivery_rate >= 95:
            delivery_score = 35
        elif delivery_rate >= 90:
            delivery_score = 30
        elif delivery_rate >= 80:
            delivery_score = 20
        else:
            delivery_score = max(0, delivery_rate / 100 * 40)
        
        # Engagement component (0-30 points)
        # Good engagement = opens + replies
        open_rate = rates["open_rate"]
        reply_rate = rates["reply_rate"]
        
        engagement_score = 0
        if open_rate >= 20:
            engagement_score += 15
        elif open_rate >= 10:
            engagement_score += 10
        elif open_rate >= 5:
            engagement_score += 5
        
        if reply_rate >= 5:
            engagement_score += 15
        elif reply_rate >= 2:
            engagement_score += 10
        elif reply_rate >= 1:
            engagement_score += 5
        
        # Complaint component (negative, 0 to -30)
        complaint_penalty = 0
        bounce_rate = rates["bounce_rate"]
        
        # Bounce penalty
        if bounce_rate >= 10:
            complaint_penalty += 20
        elif bounce_rate >= 5:
            complaint_penalty += 10
        elif bounce_rate >= 2:
            complaint_penalty += 5
        
        # Spam complaint penalty (very severe)
        if totals["spam_complaints"] > 0:
            complaint_rate = totals["spam_complaints"] / totals["sent"] * 100 if totals["sent"] > 0 else 0
            if complaint_rate >= 0.5:
                complaint_penalty += 30  # Max penalty
            elif complaint_rate >= 0.1:
                complaint_penalty += 20
            elif complaint_rate > 0:
                complaint_penalty += 10
        
        # Calculate final score
        score = delivery_score + engagement_score + (30 - complaint_penalty)
        score = max(0, min(100, score))  # Clamp to 0-100
        
        return round(score, 1)
    
    def update_reputation(self, inbox_id: int) -> Dict[str, Any]:
        """Update and record reputation for an inbox"""
        
        # Get current reputation
        new_score = self.calculate_reputation(inbox_id)
        
        # Get previous score
        last_record = self.db.query(ReputationHistory).filter(
            ReputationHistory.inbox_id == inbox_id
        ).order_by(ReputationHistory.recorded_at.desc()).first()
        
        previous_score = last_record.score if last_record else 100.0
        change = new_score - previous_score
        
        # Record history
        history = ReputationHistory(
            inbox_id=inbox_id,
            score=new_score,
            previous_score=previous_score,
            change=change,
            reason="Daily calculation"
        )
        self.db.add(history)
        
        # Update today's metrics with reputation
        metrics = self.metrics_service.get_or_create_daily_metrics(inbox_id)
        metrics.reputation_score = new_score
        
        self.db.commit()
        
        return {
            "inbox_id": inbox_id,
            "score": new_score,
            "previous_score": previous_score,
            "change": change,
            "status": self._get_status(new_score)
        }
    
    def _get_status(self, score: float) -> str:
        """Get status label for a score"""
        
        if score >= 90:
            return "excellent"
        elif score >= 70:
            return "good"
        elif score >= 50:
            return "warning"
        else:
            return "critical"
    
    def get_reputation_trend(self, inbox_id: int, days: int = 30) -> List[Dict[str, Any]]:
        """Get reputation trend over time"""
        
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        
        history = self.db.query(ReputationHistory).filter(
            ReputationHistory.inbox_id == inbox_id,
            ReputationHistory.recorded_at >= start_date
        ).order_by(ReputationHistory.recorded_at).all()
        
        return [
            {
                "date": h.recorded_at.isoformat(),
                "score": h.score,
                "change": h.change
            }
            for h in history
        ]


class SafetyService:
    """Service for auto-pause and safety checks"""
    
    def __init__(self, db: Session):
        self.db = db
        self.metrics_service = MetricsService(db)
        self.reputation_service = ReputationService(db)
    
    def check_inbox_health(self, inbox: Inbox) -> Dict[str, Any]:
        """Check if an inbox is healthy and should continue sending"""
        
        issues = []
        should_pause = False
        
        # Get today's metrics
        metrics = self.metrics_service.get_or_create_daily_metrics(inbox.id)
        
        # Check bounce rate
        if metrics.emails_sent >= 10:  # Need minimum sends to check
            if metrics.bounce_rate >= THRESHOLDS["bounce_rate_critical"]:
                issues.append({
                    "type": "bounce_rate",
                    "severity": "critical",
                    "value": metrics.bounce_rate,
                    "threshold": THRESHOLDS["bounce_rate_critical"]
                })
                should_pause = True
            elif metrics.bounce_rate >= THRESHOLDS["bounce_rate_warning"]:
                issues.append({
                    "type": "bounce_rate",
                    "severity": "warning",
                    "value": metrics.bounce_rate,
                    "threshold": THRESHOLDS["bounce_rate_warning"]
                })
        
        # Check delivery rate
        if metrics.emails_sent >= 10:
            if metrics.delivery_rate < THRESHOLDS["delivery_rate_critical"]:
                issues.append({
                    "type": "delivery_rate",
                    "severity": "critical",
                    "value": metrics.delivery_rate,
                    "threshold": THRESHOLDS["delivery_rate_critical"]
                })
                should_pause = True
            elif metrics.delivery_rate < THRESHOLDS["delivery_rate_warning"]:
                issues.append({
                    "type": "delivery_rate",
                    "severity": "warning",
                    "value": metrics.delivery_rate,
                    "threshold": THRESHOLDS["delivery_rate_warning"]
                })
        
        # Check spam complaints
        if metrics.spam_complaints > 0 and metrics.emails_sent > 0:
            complaint_rate = (metrics.spam_complaints / metrics.emails_sent) * 100
            if complaint_rate >= THRESHOLDS["spam_complaint_critical"]:
                issues.append({
                    "type": "spam_complaint",
                    "severity": "critical",
                    "value": complaint_rate,
                    "threshold": THRESHOLDS["spam_complaint_critical"]
                })
                should_pause = True
        
        # Check reputation
        reputation = self.reputation_service.calculate_reputation(inbox.id)
        if reputation < THRESHOLDS["reputation_critical"]:
            issues.append({
                "type": "reputation",
                "severity": "critical",
                "value": reputation,
                "threshold": THRESHOLDS["reputation_critical"]
            })
            should_pause = True
        elif reputation < THRESHOLDS["reputation_warning"]:
            issues.append({
                "type": "reputation",
                "severity": "warning",
                "value": reputation,
                "threshold": THRESHOLDS["reputation_warning"]
            })
        
        return {
            "inbox_id": inbox.id,
            "email": inbox.email,
            "is_healthy": len(issues) == 0,
            "should_pause": should_pause,
            "issues": issues,
            "reputation": reputation
        }
    
    def auto_pause_if_needed(self, inbox: Inbox) -> Dict[str, Any]:
        """Check health and auto-pause if critical issues found"""
        
        health = self.check_inbox_health(inbox)
        
        if health["should_pause"] and inbox.state == InboxState.WARMING_UP:
            # Pause the inbox
            inbox.state = InboxState.PAUSED
            inbox.pause_reason = f"Auto-paused due to: {', '.join(i['type'] for i in health['issues'] if i['severity'] == 'critical')}"
            inbox.paused_at = datetime.now(timezone.utc)
            
            # Create alert
            alert = Alert(
                inbox_id=inbox.id,
                alert_type=AlertType.AUTO_PAUSED,
                severity=AlertSeverity.CRITICAL,
                title=f"Inbox auto-paused: {inbox.email}",
                message=inbox.pause_reason,
                data={"issues": health["issues"]},
                action_taken="auto_paused"
            )
            self.db.add(alert)
            self.db.commit()
            
            logger.warning(f"Auto-paused inbox {inbox.email}: {inbox.pause_reason}")
            
            return {
                "action": "paused",
                "reason": inbox.pause_reason,
                "health": health
            }
        
        return {
            "action": "none",
            "health": health
        }
    
    def check_all_inboxes(self, user_id: Optional[int] = None) -> Dict[str, Any]:
        """Check health of all active inboxes"""

        query = self.db.query(Inbox).filter(
            Inbox.is_active == True,
            Inbox.state.in_([InboxState.WARMING_UP, InboxState.WARMED_UP])
        )
        if user_id is not None:
            query = query.filter(Inbox.user_id == user_id)
        inboxes = query.all()
        
        results = {
            "checked": 0,
            "healthy": 0,
            "warning": 0,
            "paused": 0,
            "details": []
        }
        
        for inbox in inboxes:
            results["checked"] += 1
            
            result = self.auto_pause_if_needed(inbox)
            health = result["health"]
            
            if result["action"] == "paused":
                results["paused"] += 1
            elif health["is_healthy"]:
                results["healthy"] += 1
            else:
                results["warning"] += 1
            
            results["details"].append({
                "inbox_id": inbox.id,
                "email": inbox.email,
                "action": result["action"],
                "is_healthy": health["is_healthy"],
                "reputation": health["reputation"]
            })
        
        return results


class HealthReportService:
    """Service for generating health reports"""
    
    def __init__(self, db: Session):
        self.db = db
        self.metrics_service = MetricsService(db)
        self.reputation_service = ReputationService(db)
        self.safety_service = SafetyService(db)
    
    def generate_daily_report(self, user_id: Optional[int] = None) -> Dict[str, Any]:
        """Generate a daily health report for all inboxes"""
        
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Get all active inboxes
        inbox_query = self.db.query(Inbox).filter(Inbox.is_active == True)
        if user_id is not None:
            inbox_query = inbox_query.filter(Inbox.user_id == user_id)
        inboxes = inbox_query.all()
        
        report = {
            "date": today.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_inboxes": len(inboxes),
                "warming_up": 0,
                "warmed_up": 0,
                "paused": 0,
                "total_sent_today": 0,
                "total_delivered_today": 0,
                "total_bounced_today": 0,
                "avg_reputation": 0
            },
            "alerts": {
                "unread": 0,
                "critical": 0,
                "recent": []
            },
            "inbox_details": []
        }
        
        reputation_sum = 0
        reputation_count = 0
        
        for inbox in inboxes:
            # Count states
            if inbox.state == InboxState.WARMING_UP:
                report["summary"]["warming_up"] += 1
            elif inbox.state == InboxState.WARMED_UP:
                report["summary"]["warmed_up"] += 1
            elif inbox.state == InboxState.PAUSED:
                report["summary"]["paused"] += 1
            
            # Get today's metrics
            metrics = self.db.query(DailyMetrics).filter(
                DailyMetrics.inbox_id == inbox.id,
                func.date(DailyMetrics.date) == today.date()
            ).first()
            
            if metrics:
                report["summary"]["total_sent_today"] += metrics.emails_sent
                report["summary"]["total_delivered_today"] += metrics.delivered
                report["summary"]["total_bounced_today"] += metrics.bounced
                
                reputation_sum += metrics.reputation_score
                reputation_count += 1
            
            # Calculate current reputation
            reputation = self.reputation_service.calculate_reputation(inbox.id)
            
            report["inbox_details"].append({
                "id": inbox.id,
                "email": inbox.email,
                "state": inbox.state.value,
                "warmup_day": inbox.warmup_day,
                "reputation": reputation,
                "today": {
                    "sent": metrics.emails_sent if metrics else 0,
                    "delivered": metrics.delivered if metrics else 0,
                    "bounced": metrics.bounced if metrics else 0,
                    "delivery_rate": metrics.delivery_rate if metrics else 100
                }
            })
        
        # Calculate average reputation
        if reputation_count > 0:
            report["summary"]["avg_reputation"] = round(reputation_sum / reputation_count, 1)
        
        # Get recent alerts
        alert_query = self.db.query(Alert).filter(
            Alert.created_at >= today - timedelta(days=1)
        )
        if user_id is not None:
            alert_query = alert_query.join(Inbox, Alert.inbox_id == Inbox.id).filter(
                Inbox.user_id == user_id
            )
        recent_alerts = alert_query.order_by(Alert.created_at.desc()).limit(10).all()
        
        report["alerts"]["recent"] = [
            {
                "id": a.id,
                "type": a.alert_type.value,
                "severity": a.severity.value,
                "title": a.title,
                "created_at": a.created_at.isoformat()
            }
            for a in recent_alerts
        ]
        
        # Count unread and critical
        unread_query = self.db.query(Alert).filter(Alert.is_read == False)
        critical_query = self.db.query(Alert).filter(
            Alert.severity == AlertSeverity.CRITICAL,
            Alert.is_resolved == False
        )
        if user_id is not None:
            unread_query = unread_query.join(Inbox, Alert.inbox_id == Inbox.id).filter(
                Inbox.user_id == user_id
            )
            critical_query = critical_query.join(Inbox, Alert.inbox_id == Inbox.id).filter(
                Inbox.user_id == user_id
            )
        report["alerts"]["unread"] = unread_query.count()
        report["alerts"]["critical"] = critical_query.count()
        
        return report
    
    def generate_inbox_report(self, inbox_id: int) -> Dict[str, Any]:
        """Generate a detailed report for a specific inbox"""
        
        inbox = self.db.query(Inbox).filter(Inbox.id == inbox_id).first()
        if not inbox:
            return {"error": "Inbox not found"}
        
        # Get 7-day metrics
        metrics_summary = self.metrics_service.get_inbox_metrics_summary(inbox_id, 7)
        
        # Get reputation trend
        reputation_trend = self.reputation_service.get_reputation_trend(inbox_id, 30)
        
        # Get current health
        health = self.safety_service.check_inbox_health(inbox)
        
        # Get recent alerts
        alerts = self.db.query(Alert).filter(
            Alert.inbox_id == inbox_id
        ).order_by(Alert.created_at.desc()).limit(10).all()
        
        return {
            "inbox": {
                "id": inbox.id,
                "email": inbox.email,
                "state": inbox.state.value,
                "warmup_day": inbox.warmup_day,
                "warmup_started": inbox.warmup_started_at.isoformat() if inbox.warmup_started_at else None
            },
            "health": health,
            "metrics_7d": metrics_summary,
            "reputation_trend": reputation_trend,
            "recent_alerts": [
                {
                    "id": a.id,
                    "type": a.alert_type.value,
                    "severity": a.severity.value,
                    "title": a.title,
                    "message": a.message,
                    "created_at": a.created_at.isoformat(),
                    "is_resolved": a.is_resolved
                }
                for a in alerts
            ]
        }
