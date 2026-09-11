from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean, Text
from sqlalchemy.sql import func

from database import Base


class WarmupThread(Base):
    """Track warm-up email threads/conversations"""
    __tablename__ = "warmup_threads"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Participants
    sender_inbox_id = Column(Integer, ForeignKey("inboxes.id"), nullable=False)
    recipient_inbox_id = Column(Integer, ForeignKey("inboxes.id"), nullable=False)
    
    # Thread info
    subject = Column(String(500), nullable=False)
    message_count = Column(Integer, default=1)
    
    # Status
    is_active = Column(Boolean, default=True)
    last_sender_id = Column(Integer, ForeignKey("inboxes.id"), nullable=True)
    
    # Timestamps
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    last_activity_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WarmupPartner(Base):
    """Track partner assignments for warm-up"""
    __tablename__ = "warmup_partners"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Partnership
    inbox_id = Column(Integer, ForeignKey("inboxes.id"), nullable=False)
    partner_inbox_id = Column(Integer, ForeignKey("inboxes.id"), nullable=False)
    
    # Rotation tracking
    assigned_at = Column(DateTime(timezone=True), server_default=func.now())
    last_rotated_at = Column(DateTime(timezone=True), nullable=True)
    rotation_count = Column(Integer, default=0)
    
    # Cooldown tracking
    last_email_sent_at = Column(DateTime(timezone=True), nullable=True)
    last_email_received_at = Column(DateTime(timezone=True), nullable=True)
    emails_sent_count = Column(Integer, default=0)
    emails_received_count = Column(Integer, default=0)
    
    # Status
    is_active = Column(Boolean, default=True)
    cooldown_until = Column(DateTime(timezone=True), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WarmupReply(Base):
    """Track pending and sent replies"""
    __tablename__ = "warmup_replies"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # References
    thread_id = Column(Integer, ForeignKey("warmup_threads.id"), nullable=True)
    original_email_id = Column(Integer, ForeignKey("emails.id"), nullable=True)
    inbox_id = Column(Integer, ForeignKey("inboxes.id"), nullable=False)
    
    # Email details
    to_email = Column(String(255), nullable=False)
    from_email = Column(String(255), nullable=False)
    subject = Column(String(500), nullable=False)
    original_subject = Column(String(500), nullable=True)
    reply_type = Column(String(50), nullable=True)  # acknowledgment, question, etc.
    
    # Scheduling
    scheduled_at = Column(DateTime(timezone=True), nullable=False)  # When to send
    sent_at = Column(DateTime(timezone=True), nullable=True)
    
    # Status
    status = Column(String(50), default="pending")  # pending, queued, sent, failed
    error_message = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class FallbackReplyEmail(Base):
    """Global fallback email for sending auto-replies when inbox IMAP/SMTP fails (e.g. MX mismatch)"""
    __tablename__ = "fallback_reply_emails"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    email = Column(String(255), nullable=False)
    app_password = Column(String(255), nullable=False)
    smtp_host = Column(String(255), nullable=True)   # auto-detected or manual
    smtp_port = Column(Integer, default=587)
    imap_host = Column(String(255), nullable=True)
    imap_port = Column(Integer, default=993)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class CampaignAutoReply(Base):
    """Track auto-replies to campaign recipients who reply"""
    __tablename__ = "campaign_auto_replies"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # References
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    campaign_recipient_id = Column(Integer, ForeignKey("campaign_recipients.id"), nullable=False)
    inbox_id = Column(Integer, ForeignKey("inboxes.id"), nullable=True)
    
    # Email details
    to_email = Column(String(255), nullable=False)
    from_email = Column(String(255), nullable=False)
    subject = Column(String(500), nullable=False)
    original_subject = Column(String(500), nullable=True)
    body_text = Column(Text, nullable=True)
    body_html = Column(Text, nullable=True)
    
    # The original reply we're responding to (for duplicate detection)
    original_reply_subject = Column(String(500), nullable=True)
    original_reply_snippet = Column(Text, nullable=True)
    original_message_id = Column(String(500), nullable=True)  # Unique message ID from IMAP

    # Thread tracking: full thread ID with sequence (MMMMMMM-CCCCCCCC-NNN)
    thread_id = Column(String(24), nullable=True, index=True)
    
    # Scheduling
    scheduled_at = Column(DateTime(timezone=True), nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    
    # Status: pending, queued, sent, failed
    status = Column(String(50), default="pending")
    error_message = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
