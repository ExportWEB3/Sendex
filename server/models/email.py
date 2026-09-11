from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, Text, ForeignKey
from sqlalchemy.sql import func
import enum

from database import Base


class EmailStatus(str, enum.Enum):
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    BOUNCED = "bounced"
    FAILED = "failed"
    OPENED = "opened"
    CLICKED = "clicked"
    REPLIED = "replied"
    UNSUBSCRIBED = "unsubscribed"
    SPAM = "spam"


class EmailType(str, enum.Enum):
    CAMPAIGN = "campaign"
    WARMUP = "warmup"
    TRANSACTIONAL = "transactional"


class BounceType(str, enum.Enum):
    NONE = "none"
    SOFT = "soft"  # Temporary failure (mailbox full, server down)
    HARD = "hard"  # Permanent failure (invalid email, domain doesn't exist)


class Email(Base):
    __tablename__ = "emails"

    id = Column(Integer, primary_key=True, index=True)
    
    # References
    smtp_account_id = Column(Integer, ForeignKey("smtp_accounts.id"), nullable=True)
    inbox_id = Column(Integer, ForeignKey("inboxes.id"), nullable=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=True)
    recipient_id = Column(Integer, ForeignKey("recipients.id"), nullable=True)
    
    # Email content
    from_email = Column(String(255), nullable=False)
    from_name = Column(String(255), nullable=True)
    to_email = Column(String(255), nullable=False, index=True)
    to_name = Column(String(255), nullable=True)
    subject = Column(String(500), nullable=False)
    body_text = Column(Text, nullable=True)
    body_html = Column(Text, nullable=True)
    
    # Type and status
    email_type = Column(Enum(EmailType), default=EmailType.CAMPAIGN)
    status = Column(Enum(EmailStatus), default=EmailStatus.QUEUED, index=True)
    
    # Bounce handling
    bounce_type = Column(Enum(BounceType), default=BounceType.NONE)
    bounce_code = Column(String(50), nullable=True)
    bounce_message = Column(Text, nullable=True)
    
    # Retry logic
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    
    # Tracking
    message_id = Column(String(255), nullable=True, index=True)  # SMTP message ID
    opened_at = Column(DateTime(timezone=True), nullable=True)
    clicked_at = Column(DateTime(timezone=True), nullable=True)
    replied_at = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    queued_at = Column(DateTime(timezone=True), server_default=func.now())
    sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
