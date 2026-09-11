"""
Email Template Schemas for API validation
"""

from pydantic import BaseModel, ConfigDict, Field, field_serializer
from typing import Optional, List, Dict, Any
from datetime import datetime

from services.attachment_service import public_attachment_metadata


class SESEmailTemplateCreate(BaseModel):
    """Schema for creating a new email template"""
    name: str = Field(..., min_length=1, max_length=255, description="Template name")
    description: Optional[str] = Field(None, description="Template description")
    category: Optional[str] = Field("custom", description="Template category")
    
    subject_line: str = Field(..., description="Email subject line (can contain {{variable}})")
    preview_text: Optional[str] = Field(None, max_length=255, description="Preview text in email clients")
    
    html_content: str = Field(..., description="HTML email design - user's custom HTML")
    text_fallback: Optional[str] = Field(None, description="Plain text fallback")
    template_type: Optional[str] = Field('html', description="Template type: 'html' or 'plain_text'")
    
    available_variables: Optional[List[str]] = Field(None, description="{{variable}} names used")
    variable_descriptions: Optional[Dict[str, str]] = Field(None, description="Help for each variable")
    
    is_public: bool = Field(False, description="Can other users use this template?")
    tags: Optional[List[str]] = Field(None, description="Tags for organizing")
    attachments: Optional[List[Dict[str, Any]]] = Field(None, description="File attachments [{filename, stored_name, filepath, content_type, size}]")


class SESEmailTemplateUpdate(BaseModel):
    """Schema for updating email template"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None)
    category: Optional[str] = Field(None)
    
    subject_line: Optional[str] = Field(None)
    preview_text: Optional[str] = Field(None, max_length=255)
    
    html_content: Optional[str] = Field(None)
    text_fallback: Optional[str] = Field(None)
    template_type: Optional[str] = Field(None, description="Template type: 'html' or 'plain_text'")
    
    available_variables: Optional[List[str]] = Field(None)
    variable_descriptions: Optional[Dict[str, str]] = Field(None)
    
    is_active: Optional[bool] = Field(None)
    is_public: Optional[bool] = Field(None)
    tags: Optional[List[str]] = Field(None)
    attachments: Optional[List[Dict[str, Any]]] = Field(None, description="File attachments")


class SESEmailTemplateResponse(BaseModel):
    """Schema for returning email template"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str]
    category: str
    
    subject_line: str
    preview_text: Optional[str]
    
    html_content: str
    text_fallback: Optional[str]
    template_type: Optional[str] = 'html'
    
    is_active: bool
    is_public: bool
    
    available_variables: Optional[List[str]]
    variable_descriptions: Optional[Dict[str, str]]
    
    created_at: datetime
    updated_at: datetime
    last_used_at: Optional[datetime]
    
    usage_count: int
    version: int
    tags: Optional[List[str]]
    attachments: Optional[List[Dict[str, Any]]]

    @field_serializer("attachments")
    def serialize_attachments(self, value):
        return None if value is None else public_attachment_metadata(value)
    
