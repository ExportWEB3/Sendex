from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import secrets

from database import Base
from models.list import RecipientStatus, recipient_tag_link


def generate_unsubscribe_token():
    """Generate a unique token for unsubscribe links"""
    return secrets.token_urlsafe(32)


class Recipient(Base):
    __tablename__ = "recipients"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, index=True)  # Removed unique, handled by list
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    company = Column(String(255), nullable=True)
    title = Column(String(255), nullable=True)  # Job title
    
    # List membership (optional - can be standalone)
    list_id = Column(Integer, ForeignKey("recipient_lists.id"), nullable=True)
    
    # Status tracking
    status = Column(SQLEnum(RecipientStatus), default=RecipientStatus.ACTIVE)
    
    # Unsubscribe token (for secure unsubscribe links)
    unsubscribe_token = Column(String(64), unique=True, default=generate_unsubscribe_token)
    
    # Legacy tags field (keeping for backward compatibility)
    tags = Column(Text, nullable=True)
    
    # Status flags
    is_suppressed = Column(Boolean, default=False)  # if unsubscribed or hard bounced
    suppression_reason = Column(String(255), nullable=True)
    
    # Duplicate tracking
    is_duplicate = Column(Boolean, default=False)
    duplicate_of_id = Column(Integer, nullable=True)  # ID of original if this is a duplicate
    
    # Engagement tracking
    total_sent = Column(Integer, default=0)
    total_opened = Column(Integer, default=0)
    total_clicked = Column(Integer, default=0)
    total_replied = Column(Integer, default=0)
    total_bounced = Column(Integer, default=0)
    
    last_sent_at = Column(DateTime(timezone=True), nullable=True)
    last_opened_at = Column(DateTime(timezone=True), nullable=True)
    last_clicked_at = Column(DateTime(timezone=True), nullable=True)
    unsubscribed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    recipient_list = relationship("RecipientList", back_populates="recipients")
    tags_rel = relationship(
        "RecipientTag",
        secondary=recipient_tag_link,
        back_populates="recipients"
    )
