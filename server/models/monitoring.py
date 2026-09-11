"""
Day 6: Monitoring & Safety Models
- Track daily metrics per inbox
- Store alerts and health events
- Reputation scoring
"""

from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, Text, ForeignKey, Enum as SQLEnum, JSON
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import enum


def _utc_now():
    return datetime.now(timezone.utc)

from database import Base


class AlertSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, enum.Enum):
    HIGH_BOUNCE_RATE = "high_bounce_rate"
    LOW_DELIVERY_RATE = "low_delivery_rate"
    SPAM_COMPLAINT = "spam_complaint"
    BLACKLIST_DETECTED = "blacklist_detected"
    REPUTATION_DROP = "reputation_drop"
    VOLUME_SPIKE = "volume_spike"
    AUTO_PAUSED = "auto_paused"
    WARMUP_COMPLETE = "warmup_complete"
    PARTNER_EXHAUSTED = "partner_exhausted"


class DailyMetrics(Base):
    """Daily metrics for each inbox"""
    __tablename__ = "daily_metrics"
    
    id = Column(Integer, primary_key=True, index=True)
    inbox_id = Column(Integer, ForeignKey("inboxes.id"), nullable=False)
    date = Column(DateTime, nullable=False)  # Date for these metrics
    
    # Volume metrics
    emails_sent = Column(Integer, default=0)
    emails_received = Column(Integer, default=0)
    warmup_emails_sent = Column(Integer, default=0)
    warmup_emails_received = Column(Integer, default=0)
    campaign_emails_sent = Column(Integer, default=0)
    
    # Delivery metrics
    delivered = Column(Integer, default=0)
    bounced = Column(Integer, default=0)
    soft_bounced = Column(Integer, default=0)
    hard_bounced = Column(Integer, default=0)
    
    # Engagement metrics
    opens = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    replies = Column(Integer, default=0)
    unsubscribes = Column(Integer, default=0)
    
    # Negative metrics
    spam_complaints = Column(Integer, default=0)
    spam_folder_rate = Column(Float, default=0.0)  # % landed in spam
    
    # Calculated rates (stored for quick access)
    delivery_rate = Column(Float, default=100.0)  # % delivered
    bounce_rate = Column(Float, default=0.0)
    open_rate = Column(Float, default=0.0)
    reply_rate = Column(Float, default=0.0)
    
    # Reputation score for the day (0-100)
    reputation_score = Column(Float, default=100.0)
    
    created_at = Column(DateTime(timezone=True), default=_utc_now)
    updated_at = Column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now)
    
    # Relationships
    inbox = relationship("Inbox", backref="daily_metrics")


class Alert(Base):
    """Alerts and notifications for monitoring"""
    __tablename__ = "alerts"
    
    id = Column(Integer, primary_key=True, index=True)
    inbox_id = Column(Integer, ForeignKey("inboxes.id"), nullable=True)  # Null for system-wide alerts
    
    alert_type = Column(SQLEnum(AlertType), nullable=False)
    severity = Column(SQLEnum(AlertSeverity), default=AlertSeverity.INFO)
    
    title = Column(String(255), nullable=False)
    message = Column(Text)
    
    # Alert data (JSON for flexible storage)
    data = Column(JSON, nullable=True)
    
    # Status
    is_read = Column(Boolean, default=False)
    is_resolved = Column(Boolean, default=False)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String(100), nullable=True)  # User or "auto"
    
    # Action taken
    action_taken = Column(String(255), nullable=True)  # e.g., "auto_paused", "notified_admin"
    
    created_at = Column(DateTime(timezone=True), default=_utc_now)
    
    # Relationships
    inbox = relationship("Inbox", backref="alerts")


class ReputationHistory(Base):
    """Track reputation changes over time"""
    __tablename__ = "reputation_history"
    
    id = Column(Integer, primary_key=True, index=True)
    inbox_id = Column(Integer, ForeignKey("inboxes.id"), nullable=False)
    
    score = Column(Float, nullable=False)  # 0-100
    previous_score = Column(Float, nullable=True)
    change = Column(Float, default=0.0)  # Positive or negative change
    
    # What caused the change
    reason = Column(String(255))
    
    # Breakdown of score components
    delivery_component = Column(Float, default=0.0)  # Max 40 points
    engagement_component = Column(Float, default=0.0)  # Max 30 points
    complaint_component = Column(Float, default=0.0)  # Max 30 points (negative impact)
    
    recorded_at = Column(DateTime(timezone=True), default=_utc_now)
    
    # Relationships
    inbox = relationship("Inbox", backref="reputation_history")


class HealthCheck(Base):
    """System health checks"""
    __tablename__ = "health_checks"
    
    id = Column(Integer, primary_key=True, index=True)
    
    check_type = Column(String(50), nullable=False)  # "smtp", "imap", "redis", "database"
    target = Column(String(255))  # What was checked (e.g., inbox email or service name)
    
    is_healthy = Column(Boolean, default=True)
    response_time_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    
    checked_at = Column(DateTime(timezone=True), default=_utc_now)
