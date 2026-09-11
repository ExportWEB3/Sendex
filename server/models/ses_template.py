"""
Email Template Model
Stores user-designed HTML email templates that can be selected for campaigns
"""

from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, JSON, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from datetime import datetime, timezone

from database import Base


class EmailTemplateCategory(str, enum.Enum):
    """Categories for organizing email templates"""
    PROMOTIONAL = "promotional"
    TRANSACTIONAL = "transactional"
    NEWSLETTER = "newsletter"
    WELCOME = "welcome"
    CUSTOM = "custom"


class SESEmailTemplate(Base):
    """
    Email template model. The class and table names are retained for database compatibility.
    
    Users design and store HTML email templates.
    When creating a campaign, users select a template.
    The campaign inherits the template's design and can customize subject/variables.
    """
    __tablename__ = "ses_email_templates"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # Owner
    
    # Template identification
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(Enum(EmailTemplateCategory), default=EmailTemplateCategory.CUSTOM)
    
    # Template design
    subject_line = Column(String(500), nullable=False)  # Can contain {{variable}}
    preview_text = Column(String(255), nullable=True)  # Preview line in email clients
    
    # HTML Design - user can design this however they want
    html_content = Column(Text, nullable=False)  # Full custom HTML with inline CSS
    text_fallback = Column(Text, nullable=True)  # Plain text version (optional)
    template_type = Column(String(20), default='html', nullable=False)  # 'html' or 'plain_text'
    
    # Template configuration
    is_active = Column(Boolean, default=True)
    is_public = Column(Boolean, default=False)  # Can other users use it?
    
    # Variables this template uses
    available_variables = Column(JSON, nullable=True)  # List of {{variable}} names
    variable_descriptions = Column(JSON, nullable=True)  # Help text for each variable
    
    # Tracking
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    # Stats
    usage_count = Column(Integer, default=0)  # How many campaigns used this
    last_used_at = Column(DateTime, nullable=True)
    
    # Attachments: [{filename, stored_name, filepath, content_type, size}]
    attachments = Column(JSON, nullable=True)
    
    # Metadata
    version = Column(Integer, default=1)
    tags = Column(JSON, nullable=True)  # For organizing templates
