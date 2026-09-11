from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from models.recipient import Recipient
from models.user import User, UserRole
from schemas.recipient import RecipientCreate, RecipientResponse, RecipientUpdate
from api.auth import require_admin, require_auth

router = APIRouter(prefix="/api/recipients", tags=["Recipients"])


def _recipient_query_for_user(db: Session, current_user: User):
    """Build a recipient query constrained to the current tenant."""
    query = db.query(Recipient)
    if current_user.role != UserRole.ADMIN:
        from models.list import RecipientList
        query = query.join(RecipientList, Recipient.list_id == RecipientList.id).filter(
            RecipientList.user_id == current_user.id
        )
    return query


@router.get("/", response_model=List[RecipientResponse])
def get_all_recipients(
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get all recipients (filtered by user ownership via list)"""
    return _recipient_query_for_user(db, current_user).all()


@router.get("/{recipient_id}", response_model=RecipientResponse)
def get_recipient(
    recipient_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get a specific recipient by ID"""
    recipient = _recipient_query_for_user(db, current_user).filter(
        Recipient.id == recipient_id
    ).first()
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")
    return recipient


@router.post("/", response_model=RecipientResponse)
def create_recipient(
    recipient_data: RecipientCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Create a new recipient"""
    recipient = Recipient(**recipient_data.model_dump())
    db.add(recipient)
    db.commit()
    db.refresh(recipient)
    return recipient


@router.put("/{recipient_id}", response_model=RecipientResponse)
def update_recipient(
    recipient_id: int,
    recipient_data: RecipientUpdate,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Update an existing recipient"""
    recipient = _recipient_query_for_user(db, current_user).filter(
        Recipient.id == recipient_id
    ).first()
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")
    
    update_data = recipient_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(recipient, field, value)
    
    db.commit()
    db.refresh(recipient)
    return recipient


@router.delete("/{recipient_id}")
def delete_recipient(
    recipient_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Delete a recipient"""
    recipient = _recipient_query_for_user(db, current_user).filter(
        Recipient.id == recipient_id
    ).first()
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")
    
    db.delete(recipient)
    db.commit()
    return {"message": "Recipient deleted successfully"}
