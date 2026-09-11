"""
Day 7: Recipient List & Tag Models
- RecipientList: Named groups for organizing recipients
- RecipientTag: Flexible labels for segmentation
- RecipientTagLink: Many-to-many relationship
"""

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Table, Boolean, Text, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from database import Base


class RecipientStatus(str, enum.Enum):
    ACTIVE = "active"
    UNSUBSCRIBED = "unsubscribed"
    BOUNCED = "bounced"
    NEEDS_REVIEW = "needs_review"  # For duplicates flagged during import
    COMPLAINED = "complained"  # Marked as spam


# Many-to-many link table for recipients and tags
recipient_tag_link = Table(
    'recipient_tag_links',
    Base.metadata,
    Column('recipient_id', Integer, ForeignKey('recipients.id', ondelete='CASCADE'), primary_key=True),
    Column('tag_id', Integer, ForeignKey('recipient_tags.id', ondelete='CASCADE'), primary_key=True)
)


class RecipientList(Base):
    """Named list/group for organizing recipients"""
    __tablename__ = "recipient_lists"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # Owner
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    
    # Stats (updated on import/changes)
    recipient_count = Column(Integer, default=0)
    active_count = Column(Integer, default=0)
    unsubscribed_count = Column(Integer, default=0)
    bounced_count = Column(Integer, default=0)
    
    # Settings
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    recipients = relationship("Recipient", back_populates="recipient_list")


class RecipientTag(Base):
    """Tags for flexible recipient segmentation"""
    __tablename__ = "recipient_tags"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    color = Column(String(7), default="#3B82F6")  # Hex color for UI
    description = Column(String(255), nullable=True)
    
    # Count of recipients with this tag
    recipient_count = Column(Integer, default=0)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships (through link table)
    recipients = relationship(
        "Recipient",
        secondary=recipient_tag_link,
        back_populates="tags_rel"
    )


class ImportJob(Base):
    """Track CSV import jobs"""
    __tablename__ = "import_jobs"
    
    id = Column(Integer, primary_key=True, index=True)
    list_id = Column(Integer, ForeignKey("recipient_lists.id"), nullable=True)
    
    filename = Column(String(255))
    status = Column(String(50), default="pending")  # pending, processing, completed, failed
    
    # Stats
    total_rows = Column(Integer, default=0)
    imported_count = Column(Integer, default=0)
    duplicate_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    
    # Error details
    errors = Column(Text, nullable=True)  # JSON list of errors
    
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
