from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, ForeignKey, UniqueConstraint, Float
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from database import Base


class InboxState(str, enum.Enum):
    NOT_STARTED = "not_started"
    WARMING_UP = "warming_up"
    WARMED_UP = "warmed_up"
    PAUSED = "paused"
    DISABLED = "disabled"


class InboxGroup(str, enum.Enum):
    A = "A"
    B = "B"
    C = "C"


class SendingMode(str, enum.Enum):
    ACTIVE = "active"           # Default pace — every new inbox starts here
    HYPER = "hyper"             # Graduated pace — after HYPER_GRADUATION_DAYS
    DISTRACTED = "distracted"   # legacy — no longer assigned
    OFFLINE = "offline"         # legacy — no longer assigned


class Inbox(Base):
    __tablename__ = "inboxes"
    __table_args__ = (
        UniqueConstraint('user_id', 'email', name='uq_inbox_user_email'),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # Owner
    email = Column(String(255), nullable=False, index=True)
    smtp_account_id = Column(Integer, ForeignKey("smtp_accounts.id"), nullable=False)
    
    # IMAP settings (for reading replies)
    imap_host = Column(String(255), nullable=True)
    imap_port = Column(Integer, default=993)
    imap_username = Column(String(255), nullable=True)
    imap_password = Column(String(255), nullable=True)
    
    # Reply system toggle - auto-disabled when no IMAP is configured
    reply_enabled = Column(Boolean, default=False)
    
    # Reply-to address - when set, outgoing emails use this as Reply-To
    # and IMAP checks happen on the reply-to inbox instead
    reply_to_email = Column(String(255), nullable=True)
    
    @property
    def has_imap(self) -> bool:
        """Check if IMAP credentials are configured"""
        return bool(self.imap_host and self.imap_username and self.imap_password)
    
    # Warm-up configuration
    group = Column(Enum(InboxGroup), default=InboxGroup.A)
    state = Column(Enum(InboxState), default=InboxState.NOT_STARTED)
    warmup_start_date = Column(DateTime(timezone=True), nullable=True)
    warmup_day = Column(Integer, default=0)  # Current day in warm-up cycle
    
    # Volume tracking
    daily_cap = Column(Integer, default=5)  # Current daily limit (increases during warm-up)
    current_daily_count = Column(Integer, default=0)
    total_sent = Column(Integer, default=0)
    total_received = Column(Integer, default=0)
    total_replied = Column(Integer, default=0)
    
    # Health metrics
    bounce_rate = Column(Integer, default=0)  # Stored as percentage * 100
    spam_rate = Column(Integer, default=0)
    open_rate = Column(Integer, default=0)
    reply_rate = Column(Integer, default=0)
    
    # Status
    is_active = Column(Boolean, default=True)
    last_sent_at = Column(DateTime(timezone=True), nullable=True)
    last_received_at = Column(DateTime(timezone=True), nullable=True)
    pause_reason = Column(String(500), nullable=True)
    
    # ── Sending Mode Engine ──
    sending_mode = Column(Enum(SendingMode), default=SendingMode.ACTIVE)  # Current behavioural mode
    health_score = Column(Float, default=0.0)             # 0-100, computed from monitoring data
    last_mode_change = Column(DateTime(timezone=True), nullable=True)
    mode_locked_until = Column(DateTime(timezone=True), nullable=True)  # Prevents rapid mode flipping
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
