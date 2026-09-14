"""
Email Template API Endpoints
CRUD operations for managing user-designed email templates
"""

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timezone
import html
import logging
import re
from pathlib import Path

from database import get_db
from models.user import User, UserRole
from models.ses_template import SESEmailTemplate
from schemas.fancy_email_template import (
    SESEmailTemplateCreate,
    SESEmailTemplateUpdate,
    SESEmailTemplateResponse
)
from services.attachment_service import (
    normalize_attachment_metadata,
    public_attachment_metadata,
    store_attachment,
)
from api.auth import require_auth

router = APIRouter(prefix="/api/email-templates", tags=["Email Templates"])

logger = logging.getLogger(__name__)

SUBJECT_LINE_RE = re.compile(r"^subject:\s*(.*)$", re.IGNORECASE)

def get_effective_user_id(current_user: User, user_id: Optional[int] = None) -> int:
    """Get effective user ID (admin can view other users' data)"""
    if user_id and current_user.role == UserRole.ADMIN:
        return user_id
    return current_user.id


# ============================================================================
# CRUD Operations
# ============================================================================

@router.post("", response_model=SESEmailTemplateResponse)
def create_template(
    data: SESEmailTemplateCreate,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Create a new email template"""
    
    # Check if template name already exists
    existing = db.query(SESEmailTemplate).filter(
        SESEmailTemplate.name == data.name,
        SESEmailTemplate.user_id == current_user.id
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Template with name '{data.name}' already exists"
        )

    try:
        attachments = normalize_attachment_metadata(data.attachments)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    
    template = SESEmailTemplate(
        user_id=current_user.id,
        name=data.name,
        description=data.description,
        category=data.category,
        subject_line=data.subject_line,
        preview_text=data.preview_text,
        html_content=data.html_content,
        text_fallback=data.text_fallback,
        available_variables=data.available_variables,
        variable_descriptions=data.variable_descriptions,
        is_public=data.is_public,
        tags=data.tags,
        attachments=attachments,
    )
    
    db.add(template)
    db.commit()
    db.refresh(template)

    return template


def _unique_template_name(db: Session, user_id: int, base_name: str) -> str:
    """Auto-suffix a template name until it doesn't collide with an existing one."""
    name = base_name
    suffix = 1
    while db.query(SESEmailTemplate).filter(
        SESEmailTemplate.name == name,
        SESEmailTemplate.user_id == user_id,
    ).first():
        suffix += 1
        candidate = f"{base_name} ({suffix})"
        name = candidate[:255]
    return name


@router.post("/import-multi")
async def import_templates_multi(
    files: List[UploadFile] = File(...),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Create one email template per uploaded file. Each file's first 'Subject:'
    line becomes the template's subject; everything after it becomes the body."""
    results = []

    for file in files:
        filename = file.filename or "Untitled template"
        try:
            content = await file.read()
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                text = content.decode("latin-1")

            lines = text.splitlines()
            subject_line = None
            body_start = None
            for i, line in enumerate(lines):
                if not line.strip():
                    continue
                match = SUBJECT_LINE_RE.match(line.strip())
                if match:
                    subject_line = match.group(1).strip()
                    body_start = i + 1
                break  # only the first non-blank line is checked

            if subject_line is None:
                results.append({
                    "filename": filename,
                    "template_name": None,
                    "template_id": None,
                    "subject_line": None,
                    "errors": ["No 'Subject:' line found on the first line of the file."],
                })
                continue

            # Skip exactly one blank separator line right after the subject, if present.
            if body_start < len(lines) and not lines[body_start].strip():
                body_start += 1
            body = "\n".join(lines[body_start:]).strip("\n")

            base_name = Path(filename).stem.strip()[:255] or "Untitled template"
            name = _unique_template_name(db, current_user.id, base_name)
            html_content = html.escape(body).replace("\n", "<br>\n")

            template = SESEmailTemplate(
                user_id=current_user.id,
                name=name,
                description=None,
                category="custom",
                subject_line=subject_line,
                html_content=html_content,
                text_fallback=body,
                template_type="plain_text",
            )
            db.add(template)
            db.commit()
            db.refresh(template)

            results.append({
                "filename": filename,
                "template_name": name,
                "template_id": template.id,
                "subject_line": subject_line,
                "errors": [],
            })
        except Exception as exc:
            db.rollback()
            logger.exception("Template import failed for %s", filename)
            results.append({
                "filename": filename,
                "template_name": None,
                "template_id": None,
                "subject_line": None,
                "errors": ["An unexpected error occurred while importing this file."],
            })

    return {"results": results}


@router.get("", response_model=List[SESEmailTemplateResponse])
def list_templates(
    category: Optional[str] = Query(None, description="Filter by category"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    search: Optional[str] = Query(None, description="Search by name or description"),
    user_id: Optional[int] = Query(None, description="Admin only: view another user's templates"),
    limit: int = Query(50, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """List all email templates with optional filters"""
    
    effective_user_id = get_effective_user_id(current_user, user_id)
    
    query = db.query(SESEmailTemplate).filter(
        SESEmailTemplate.user_id == effective_user_id
    ).order_by(
        SESEmailTemplate.created_at.desc(),
        SESEmailTemplate.id.desc(),
    )
    
    if category:
        query = query.filter(SESEmailTemplate.category == category)
    
    if is_active is not None:
        query = query.filter(SESEmailTemplate.is_active == is_active)
    
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (SESEmailTemplate.name.ilike(search_term)) |
            (SESEmailTemplate.description.ilike(search_term))
        )
    
    templates = query.limit(limit).offset(offset).all()
    return templates


@router.get("/{template_id}", response_model=SESEmailTemplateResponse)
def get_template(
    template_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get a specific email template by ID"""
    
    template = db.query(SESEmailTemplate).filter(
        SESEmailTemplate.id == template_id,
        SESEmailTemplate.user_id == current_user.id
    ).first()
    
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    return template


@router.put("/{template_id}", response_model=SESEmailTemplateResponse)
def update_template(
    template_id: int,
    data: SESEmailTemplateUpdate,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Update an email template"""
    
    template = db.query(SESEmailTemplate).filter(
        SESEmailTemplate.id == template_id,
        SESEmailTemplate.user_id == current_user.id
    ).first()
    
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    # Check if new name conflicts
    if data.name and data.name != template.name:
        existing = db.query(SESEmailTemplate).filter(
            SESEmailTemplate.name == data.name,
            SESEmailTemplate.user_id == current_user.id,
            SESEmailTemplate.id != template_id
        ).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Template with name '{data.name}' already exists"
            )
    
    # Update fields
    update_data = data.model_dump(exclude_unset=True)
    if "attachments" in update_data:
        try:
            update_data["attachments"] = normalize_attachment_metadata(update_data["attachments"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    for field, value in update_data.items():
        setattr(template, field, value)
    
    template.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(template)
    
    return template


@router.delete("/{template_id}")
def delete_template(
    template_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Delete an email template"""
    
    template = db.query(SESEmailTemplate).filter(
        SESEmailTemplate.id == template_id,
        SESEmailTemplate.user_id == current_user.id
    ).first()
    
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    db.delete(template)
    db.commit()
    
    return {
        "success": True,
        "message": "Template deleted successfully"
    }


# ============================================================================
# Utility Endpoints
# ============================================================================

@router.post("/{template_id}/preview")
def preview_template(
    template_id: int,
    test_data: Optional[dict] = None,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """
    Preview a template with test data
    Replaces {{variables}} with test values
    """
    
    template = db.query(SESEmailTemplate).filter(
        SESEmailTemplate.id == template_id,
        SESEmailTemplate.user_id == current_user.id
    ).first()
    
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    # Use provided test data or defaults
    data = test_data or {}
    
    # Simple variable replacement
    subject = template.subject_line
    html = template.html_content
    
    for var_name, var_value in data.items():
        placeholder = "{{" + var_name + "}}"
        subject = subject.replace(placeholder, str(var_value))
        html = html.replace(placeholder, str(var_value))
    
    return {
        "subject": subject,
        "html": html,
        "text": template.text_fallback,
        "preview_text": template.preview_text
    }


@router.get("/categories/list")
def get_categories():
    """Get list of available template categories"""
    return {
        "categories": [
            "promotional",
            "transactional",
            "newsletter",
            "welcome",
            "custom"
        ]
    }


@router.post("/{template_id}/duplicate")
def duplicate_template(
    template_id: int,
    name: str = Query(..., min_length=1, description="Name for duplicated template"),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Duplicate an existing template"""
    
    original = db.query(SESEmailTemplate).filter(
        SESEmailTemplate.id == template_id,
        SESEmailTemplate.user_id == current_user.id
    ).first()
    
    if not original:
        raise HTTPException(status_code=404, detail="Template not found")
    
    # Check if new name exists
    existing = db.query(SESEmailTemplate).filter(
        SESEmailTemplate.name == name,
        SESEmailTemplate.user_id == current_user.id
    ).first()
    
    if existing:
        raise HTTPException(status_code=400, detail="Template name already exists")
    
    # Create duplicate
    duplicate = SESEmailTemplate(
        user_id=current_user.id,
        name=name,
        description=f"Copy of {original.name}",
        category=original.category,
        subject_line=original.subject_line,
        preview_text=original.preview_text,
        html_content=original.html_content,
        text_fallback=original.text_fallback,
        available_variables=original.available_variables,
        variable_descriptions=original.variable_descriptions,
        is_public=original.is_public,
        tags=original.tags,
        attachments=original.attachments,
    )
    
    db.add(duplicate)
    db.commit()
    db.refresh(duplicate)
    
    return {
        "success": True,
        "template_id": duplicate.id,
        "name": duplicate.name
    }


# ============================================================================
# Template File Attachments
# ============================================================================

@router.post("/upload-attachment")
async def upload_template_attachment(
    file: UploadFile = File(...),
    current_user: User = Depends(require_auth),
):
    """Upload a file attachment for a template. Returns file metadata."""
    content = await file.read()
    try:
        metadata = store_attachment(content, file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, **public_attachment_metadata([metadata])[0]}
