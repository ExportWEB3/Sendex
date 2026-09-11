"""
Day 7: Tag API Endpoints
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional

from database import get_db
from models.recipient import Recipient
from models.list import RecipientTag
from models.user import User
from schemas.list import RecipientTagCreate, RecipientTagUpdate
from api.auth import require_admin

router = APIRouter(prefix="/api/tags", tags=["Tags"])


@router.get("/")
def get_tags(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Get all tags"""
    
    tags = db.query(RecipientTag).all()
    
    return [
        {
            "id": t.id,
            "name": t.name,
            "color": t.color,
            "description": t.description,
            "recipient_count": t.recipient_count,
            "created_at": t.created_at.isoformat() if t.created_at else None
        }
        for t in tags
    ]


@router.post("/")
def create_tag(
    data: RecipientTagCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a new tag"""
    
    # Check if exists
    existing = db.query(RecipientTag).filter(RecipientTag.name == data.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Tag with this name already exists")
    
    tag = RecipientTag(
        name=data.name,
        color=data.color or "#3B82F6",
        description=data.description
    )
    db.add(tag)
    db.commit()
    db.refresh(tag)
    
    return {
        "id": tag.id,
        "name": tag.name,
        "color": tag.color,
        "description": tag.description,
        "recipient_count": 0
    }


@router.get("/{tag_id}")
def get_tag(
    tag_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Get a specific tag"""
    
    tag = db.query(RecipientTag).filter(RecipientTag.id == tag_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    return {
        "id": tag.id,
        "name": tag.name,
        "color": tag.color,
        "description": tag.description,
        "recipient_count": tag.recipient_count,
        "created_at": tag.created_at.isoformat() if tag.created_at else None
    }


@router.put("/{tag_id}")
def update_tag(
    tag_id: int,
    data: RecipientTagUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update a tag"""
    
    tag = db.query(RecipientTag).filter(RecipientTag.id == tag_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    if data.name is not None:
        tag.name = data.name
    if data.color is not None:
        tag.color = data.color
    if data.description is not None:
        tag.description = data.description
    
    db.commit()
    
    return {"updated": True, "id": tag_id}


@router.delete("/{tag_id}")
def delete_tag(
    tag_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete a tag (removes from all recipients)"""
    
    tag = db.query(RecipientTag).filter(RecipientTag.id == tag_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    db.delete(tag)
    db.commit()
    
    return {"deleted": True, "id": tag_id}


@router.get("/{tag_id}/recipients")
def get_tag_recipients(
    tag_id: int,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Get recipients with this tag"""
    
    tag = db.query(RecipientTag).filter(RecipientTag.id == tag_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    # Get recipients through relationship
    recipients = tag.recipients[skip:skip + limit]
    
    return {
        "tag_id": tag_id,
        "tag_name": tag.name,
        "total": len(tag.recipients),
        "recipients": [
            {
                "id": r.id,
                "email": r.email,
                "first_name": r.first_name,
                "last_name": r.last_name,
                "company": r.company,
                "status": r.status.value if r.status else "active"
            }
            for r in recipients
        ]
    }
