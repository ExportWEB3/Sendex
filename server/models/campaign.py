from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, Text, ForeignKey, JSON, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from database import Base


class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class RecipientSendStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    OPENED = "opened"
    CLICKED = "clicked"
    REPLIED = "replied"
    BOUNCED = "bounced"
    FAILED = "failed"
    UNSUBSCRIBED = "unsubscribed"


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # Owner
    name = Column(String(255), nullable=False)
    subject = Column(String(500), nullable=False)
    body_text = Column(Text, nullable=True)
    body_html = Column(Text, nullable=True)
    
    # Sender configuration
    from_name = Column(String(255), nullable=True)  # Override inbox from_name
    reply_to_email = Column(String(255), nullable=True)   # Custom reply-to
    
    # Recipient list
    list_id = Column(Integer, ForeignKey("recipient_lists.id"), nullable=True)
    tag_id = Column(Integer, ForeignKey("recipient_tags.id"), nullable=True)  # Or send to tag
    
    # Inbox selection (JSON array of inbox IDs)
    inbox_ids = Column(JSON, nullable=True)  # Specific inboxes to use
    use_warmup_inboxes_only = Column(Boolean, default=True)  # Only use WARMED_UP inboxes
    
    # Template data (JSON - custom variables for templates)
    template_data = Column(JSON, nullable=True)
    
    # Multi-template rotation (JSON array of email-template IDs)
    template_ids = Column(JSON, nullable=True)  # e.g. [3, 7, 1, 5]
    template_rotation_state = Column(JSON, nullable=True)  # Rotation tracking state
    
    # Status
    status = Column(Enum(CampaignStatus), default=CampaignStatus.DRAFT)
    pause_reason = Column(String(255), nullable=True)
    
    # Scheduling
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    paused_at = Column(DateTime(timezone=True), nullable=True)
    
    # Progress tracking (JSON with sent, failed, pending counts)
    send_progress = Column(JSON, nullable=True)
    current_position = Column(Integer, default=0)  # Last processed recipient index
    
    # Stats
    total_recipients = Column(Integer, default=0)
    total_sent = Column(Integer, default=0)
    total_failed = Column(Integer, default=0)
    total_delivered = Column(Integer, default=0)
    total_bounces = Column(Integer, default=0)
    total_opens = Column(Integer, default=0)
    total_clicks = Column(Integer, default=0)
    total_replies = Column(Integer, default=0)
    total_unsubscribes = Column(Integer, default=0)
    
    # Rate limiting
    daily_send_limit = Column(Integer, default=1000)
    hourly_send_limit = Column(Integer, default=100)
    delay_between_emails = Column(Integer, default=30)  # Seconds between emails
    
    # Tracking settings (disabled by default to avoid spam filters)
    track_opens = Column(Boolean, default=False)
    track_clicks = Column(Boolean, default=False)
    include_unsubscribe = Column(Boolean, default=True)
    
    # Attachments (JSON array of {filename, filepath, content_type, size})
    attachments = Column(JSON, nullable=True)
    
    # Engine type: 'smtp' (direct SMTP) or legacy 'ses' (Resend API)
    engine_type = Column(String(10), nullable=True, index=True)
    
    # Send timezone: 'US/Eastern' or 'US/Pacific' — determines which worker processes this campaign
    send_timezone = Column(String(30), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    recipient_list = relationship("RecipientList", foreign_keys=[list_id])
    recipient_tag = relationship("RecipientTag", foreign_keys=[tag_id])
    recipients = relationship("CampaignRecipient", back_populates="campaign")


class CampaignRecipient(Base):
    """
    Tracks individual recipient send status for a campaign.
    
    Each recipient in a campaign gets one CampaignRecipient record
    that tracks their status through the send pipeline.
    """
    __tablename__ = "campaign_recipients"
    __table_args__ = (
        Index("ix_campaign_recipients_pending_lookup", "campaign_id", "status", "queued_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False, index=True)
    recipient_id = Column(Integer, ForeignKey("recipients.id"), nullable=False, index=True)
    inbox_id = Column(Integer, ForeignKey("inboxes.id"), nullable=True)  # Which inbox sent this
    
    # Status
    status = Column(Enum(RecipientSendStatus), default=RecipientSendStatus.PENDING)
    error_message = Column(Text, nullable=True)
    
    # Timestamps
    queued_at = Column(DateTime(timezone=True), nullable=True)  # Set when added to Redis queue
    sent_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    opened_at = Column(DateTime(timezone=True), nullable=True)
    clicked_at = Column(DateTime(timezone=True), nullable=True)
    replied_at = Column(DateTime(timezone=True), nullable=True)
    bounced_at = Column(DateTime(timezone=True), nullable=True)
    unsubscribed_at = Column(DateTime(timezone=True), nullable=True)
    
    # The Message-ID returned by the sending service (Resend/SMTP).
    # Used for deterministic reply matching via In-Reply-To header.
    sent_message_id = Column(String(255), nullable=True, index=True)

    # Thread ID for conversation tracking: MMMMMMM-CCCCCCCC format
    # MMMMMMM = 7-char campaign batch ID, CCCCCCCC = 8-char hash of recipient email
    thread_id = Column(String(20), nullable=True, index=True)

    # Template used (for multi-template rotation tracking)
    template_id = Column(Integer, ForeignKey("ses_email_templates.id"), nullable=True)
    
    # Snapshot of sent content (for preview/audit trail)
    # These are captured at send time and immutable
    sent_subject = Column(String(500), nullable=True)  # Subject line that was actually sent
    sent_body_html = Column(Text, nullable=True)  # HTML body that was actually sent (rendered with variables)
    sent_from_email = Column(String(255), nullable=True)  # From email address used
    sent_from_name = Column(String(255), nullable=True)  # From name used
    
    # Tracking counts
    open_count = Column(Integer, default=0)
    click_count = Column(Integer, default=0)
    clicked_urls = Column(JSON, nullable=True)  # List of clicked URLs
    
    # Relationships
    campaign = relationship("Campaign", back_populates="recipients")
    recipient = relationship("Recipient")
    inbox = relationship("Inbox")
