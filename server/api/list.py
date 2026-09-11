"""
List & Tag API Endpoints with multi-tenancy support.
- CRUD for lists and tags
- CSV import/export
- Bulk recipient operations

Each user can only access their own lists.
Admins can view any user's lists via ?user_id=X parameter.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from typing import Optional, List

from database import get_db
from models.recipient import Recipient
from models.list import RecipientList, RecipientTag, RecipientStatus
from models.user import User, UserRole
from schemas.list import (
    RecipientListCreate, RecipientListUpdate, RecipientListResponse,
    RecipientTagCreate, RecipientTagUpdate, RecipientTagResponse,
    ImportRequest, ImportResponse, RecipientBulkAction
)
from services.list_service import CSVImportService
from api.auth import require_auth

router = APIRouter(prefix="/api/lists", tags=["Lists & Recipients"])

logger = logging.getLogger(__name__)


def get_effective_user_id(current_user: User, requested_user_id: Optional[int] = None) -> int:
    """Get the effective user_id for queries."""
    if requested_user_id is not None and current_user.role == UserRole.ADMIN:
        return requested_user_id
    return current_user.id


def check_list_ownership(recipient_list: RecipientList, current_user: User):
    """Check if user has access to list. Raises 403 if not."""
    if recipient_list.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")


# ==================== List CRUD ====================

@router.get("/")
def get_lists(
    skip: int = 0,
    limit: int = 100,
    user_id: Optional[int] = Query(None, description="Admin only: view another user's lists"),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get all recipient lists for current user"""
    effective_user_id = get_effective_user_id(current_user, user_id)
    
    lists = db.query(RecipientList).filter(
        RecipientList.user_id == effective_user_id
    ).offset(skip).limit(limit).all()
    
    return [
        {
            "id": l.id,
            "name": l.name,
            "description": l.description,
            "recipient_count": l.recipient_count,
            "active_count": l.active_count,
            "unsubscribed_count": l.unsubscribed_count,
            "bounced_count": l.bounced_count,
            "is_active": l.is_active,
            "created_at": l.created_at.isoformat() if l.created_at else None
        }
        for l in lists
    ]


@router.post("/")
def create_list(
    data: RecipientListCreate,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Create a new recipient list"""
    
    # Check if name exists for this user
    existing = db.query(RecipientList).filter(
        RecipientList.name == data.name,
        RecipientList.user_id == current_user.id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="List with this name already exists")
    
    recipient_list = RecipientList(
        name=data.name,
        description=data.description,
        user_id=current_user.id  # Assign to current user
    )
    db.add(recipient_list)
    db.commit()
    db.refresh(recipient_list)
    
    return {
        "id": recipient_list.id,
        "name": recipient_list.name,
        "description": recipient_list.description,
        "recipient_count": 0,
        "created_at": recipient_list.created_at.isoformat() if recipient_list.created_at else None
    }


@router.get("/{list_id}")
def get_list(
    list_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get a specific list with stats"""
    
    recipient_list = db.query(RecipientList).filter(RecipientList.id == list_id).first()
    if not recipient_list:
        raise HTTPException(status_code=404, detail="List not found")
    
    check_list_ownership(recipient_list, current_user)
    
    return {
        "id": recipient_list.id,
        "name": recipient_list.name,
        "description": recipient_list.description,
        "recipient_count": recipient_list.recipient_count,
        "active_count": recipient_list.active_count,
        "unsubscribed_count": recipient_list.unsubscribed_count,
        "bounced_count": recipient_list.bounced_count,
        "is_active": recipient_list.is_active,
        "created_at": recipient_list.created_at.isoformat() if recipient_list.created_at else None
    }


@router.put("/{list_id}")
def update_list(
    list_id: int,
    data: RecipientListUpdate,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Update a list"""
    
    recipient_list = db.query(RecipientList).filter(RecipientList.id == list_id).first()
    if not recipient_list:
        raise HTTPException(status_code=404, detail="List not found")
    
    check_list_ownership(recipient_list, current_user)
    
    if data.name is not None:
        recipient_list.name = data.name
    if data.description is not None:
        recipient_list.description = data.description
    if data.is_active is not None:
        recipient_list.is_active = data.is_active
    
    db.commit()
    
    return {"updated": True, "id": list_id}


@router.delete("/{list_id}")
def delete_list(
    list_id: int,
    delete_recipients: bool = False,
    force: bool = False,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Delete a list (optionally delete recipients too). Use force=true to also detach campaigns and import jobs."""
    from models.campaign import Campaign
    
    recipient_list = db.query(RecipientList).filter(RecipientList.id == list_id).first()
    if not recipient_list:
        raise HTTPException(status_code=404, detail="List not found")
    
    check_list_ownership(recipient_list, current_user)
    
    # Check for linked campaigns/import_jobs
    linked_campaigns = db.query(Campaign).filter(Campaign.list_id == list_id).count()
    
    if linked_campaigns > 0 and not force:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete: {linked_campaigns} campaign(s) reference this list. Use force=true to detach campaigns and delete."
        )
    
    if force:
        # Detach campaigns from this list (set list_id to NULL)
        db.query(Campaign).filter(Campaign.list_id == list_id).update({"list_id": None})
        # Delete import jobs for this list
        from models.list import ImportJob
        db.query(ImportJob).filter(ImportJob.list_id == list_id).delete()
        # Force implies delete_recipients
        delete_recipients = True
    
    if delete_recipients:
        # Detach send-tracking records that FK to these recipients before deleting them,
        # mirroring how CampaignService.delete_campaign cleans up the same tables.
        from models.campaign import CampaignRecipient
        from models.warmup import CampaignAutoReply
        from models.email import Email

        recipient_ids = [
            row.id for row in db.query(Recipient.id).filter(Recipient.list_id == list_id).all()
        ]
        if recipient_ids:
            campaign_recipient_ids = [
                row.id for row in db.query(CampaignRecipient.id).filter(
                    CampaignRecipient.recipient_id.in_(recipient_ids)
                ).all()
            ]
            if campaign_recipient_ids:
                db.query(CampaignAutoReply).filter(
                    CampaignAutoReply.campaign_recipient_id.in_(campaign_recipient_ids)
                ).delete(synchronize_session=False)
                db.query(CampaignRecipient).filter(
                    CampaignRecipient.id.in_(campaign_recipient_ids)
                ).delete(synchronize_session=False)
            db.query(Email).filter(Email.recipient_id.in_(recipient_ids)).delete(synchronize_session=False)

        # Delete all recipients in the list
        db.query(Recipient).filter(Recipient.list_id == list_id).delete(synchronize_session=False)
    else:
        # Just remove list association
        db.query(Recipient).filter(Recipient.list_id == list_id).update({"list_id": None})
    
    db.delete(recipient_list)
    db.commit()
    
    return {"deleted": True, "id": list_id}


# ==================== List Recipients ====================

@router.post("/{list_id}/recipients")
def add_recipients(
    list_id: int,
    data: dict,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Add recipients to a list directly"""
    
    recipient_list = db.query(RecipientList).filter(RecipientList.id == list_id).first()
    if not recipient_list:
        raise HTTPException(status_code=404, detail="List not found")
    
    check_list_ownership(recipient_list, current_user)
    
    recipients_data = data.get("recipients", [])
    if not recipients_data:
        raise HTTPException(status_code=400, detail="No recipients provided")
    
    added = 0
    for r in recipients_data:
        email = r.get("email", "").strip().lower()
        if not email:
            continue
            
        # Check for existing recipient
        existing = db.query(Recipient).filter(
            Recipient.list_id == list_id,
            Recipient.email == email
        ).first()
        
        if existing:
            continue  # Skip duplicates
        
        recipient = Recipient(
            list_id=list_id,
            email=email,
            first_name=r.get("first_name"),
            last_name=r.get("last_name"),
            status=RecipientStatus.ACTIVE
        )
        db.add(recipient)
        added += 1
    
    if added > 0:
        recipient_list.recipient_count = (recipient_list.recipient_count or 0) + added
        recipient_list.active_count = (recipient_list.active_count or 0) + added
        db.commit()
    
    return {"added": added, "list_id": list_id}


@router.get("/{list_id}/recipients")
def get_list_recipients(
    list_id: int,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get recipients in a list"""
    
    recipient_list = db.query(RecipientList).filter(RecipientList.id == list_id).first()
    if not recipient_list:
        raise HTTPException(status_code=404, detail="List not found")
    
    check_list_ownership(recipient_list, current_user)
    
    query = db.query(Recipient).filter(Recipient.list_id == list_id)
    
    if status:
        try:
            status_enum = RecipientStatus(status)
            query = query.filter(Recipient.status == status_enum)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid recipient status")
    
    total = query.count()
    recipients = query.offset(skip).limit(limit).all()
    
    return {
        "list_id": list_id,
        "list_name": recipient_list.name,
        "total": total,
        "recipients": [
            {
                "id": r.id,
                "email": r.email,
                "first_name": r.first_name,
                "last_name": r.last_name,
                "company": r.company,
                "title": r.title,
                "status": r.status.value if r.status else "active",
                "is_duplicate": r.is_duplicate,
                "total_sent": r.total_sent,
                "total_opened": r.total_opened
            }
            for r in recipients
        ]
    }


# ==================== CSV Import/Export ====================

@router.post("/{list_id}/import")
async def import_csv(
    list_id: int,
    file: UploadFile = File(...),
    handle_duplicates: str = Query(default="flag", pattern="^(flag|skip|update)$"),
    tag_ids: Optional[str] = None,  # Comma-separated tag IDs
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Import recipients from CSV file"""
    
    # Verify list exists
    recipient_list = db.query(RecipientList).filter(RecipientList.id == list_id).first()
    if not recipient_list:
        raise HTTPException(status_code=404, detail="List not found")
    
    check_list_ownership(recipient_list, current_user)
    
    # Read file
    content = await file.read()
    try:
        content = content.decode('utf-8')
    except UnicodeDecodeError:
        try:
            content = content.decode('latin-1')
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Unable to decode file. Please use UTF-8 encoding.")
    
    # Parse tag IDs
    tag_id_list = []
    if tag_ids:
        try:
            tag_id_list = [int(t.strip()) for t in tag_ids.split(',')]
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid tag_ids format. Use comma-separated integers.")
    
    # Parse CSV
    import_service = CSVImportService(db)
    rows, parse_errors = import_service.parse_csv(content)
    
    if not rows:
        return {
            "success": False,
            "message": "No valid rows found",
            "parse_errors": parse_errors
        }
    
    # Import
    stats = import_service.import_recipients(
        rows=rows,
        list_id=list_id,
        tag_ids=tag_id_list,
        handle_duplicates=handle_duplicates
    )
    
    stats["parse_errors"] = parse_errors
    stats["success"] = True

    return stats


@router.post("/import-multi")
async def import_csv_multi(
    files: List[UploadFile] = File(...),
    handle_duplicates: str = Query(default="flag", pattern="^(flag|skip|update)$"),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Import multiple CSV files at once, one recipient list per file named after its filename.

    A file whose derived name matches an existing list adds its recipients into that
    list instead of creating a duplicate.
    """
    import_service = CSVImportService(db)
    results = []

    for file in files:
        list_name = Path(file.filename or "Untitled list").stem.strip()[:255] or "Untitled list"
        try:
            content = await file.read()
            try:
                content = content.decode('utf-8')
            except UnicodeDecodeError:
                content = content.decode('latin-1')

            rows, parse_errors = import_service.parse_csv(content)
            if not rows:
                results.append({
                    "filename": file.filename,
                    "list_name": list_name,
                    "list_id": None,
                    "list_created": False,
                    "imported": 0,
                    "duplicates": 0,
                    "updated": 0,
                    "skipped": 0,
                    "errors": parse_errors,
                })
                continue

            recipient_list = db.query(RecipientList).filter(
                RecipientList.name == list_name,
                RecipientList.user_id == current_user.id
            ).first()
            list_created = False
            if not recipient_list:
                recipient_list = RecipientList(
                    name=list_name,
                    description=f"Imported from {file.filename}",
                    user_id=current_user.id
                )
                db.add(recipient_list)
                db.commit()
                db.refresh(recipient_list)
                list_created = True

            stats = import_service.import_recipients(
                rows=rows,
                list_id=recipient_list.id,
                tag_ids=[],
                handle_duplicates=handle_duplicates
            )

            results.append({
                "filename": file.filename,
                "list_name": list_name,
                "list_id": recipient_list.id,
                "list_created": list_created,
                "imported": stats.get("imported", 0),
                "duplicates": stats.get("duplicates", 0),
                "updated": stats.get("updated", 0),
                "skipped": stats.get("skipped", 0),
                "errors": stats.get("errors", []) + parse_errors,
            })
        except UnicodeDecodeError:
            results.append({
                "filename": file.filename,
                "list_name": list_name,
                "list_id": None,
                "list_created": False,
                "imported": 0,
                "duplicates": 0,
                "updated": 0,
                "skipped": 0,
                "errors": ["Unable to decode file. Please use UTF-8 encoding."],
            })
        except Exception:
            db.rollback()
            logger.exception("Multi-file import failed for %s", file.filename)
            results.append({
                "filename": file.filename,
                "list_name": list_name,
                "list_id": None,
                "list_created": False,
                "imported": 0,
                "duplicates": 0,
                "updated": 0,
                "skipped": 0,
                "errors": ["An unexpected error occurred while importing this file."],
            })

    return {"results": results}


@router.get("/{list_id}/export", response_class=PlainTextResponse)
def export_csv(
    list_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Export list recipients to CSV"""
    
    recipient_list = db.query(RecipientList).filter(RecipientList.id == list_id).first()
    if not recipient_list:
        raise HTTPException(status_code=404, detail="List not found")
    
    check_list_ownership(recipient_list, current_user)
    
    import_service = CSVImportService(db)
    csv_content = import_service.export_list_to_csv(list_id)
    
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={recipient_list.name}.csv"}
    )


# ==================== Duplicates Review ====================

@router.get("/{list_id}/duplicates")
def get_duplicates(
    list_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get duplicates needing review in a list"""
    
    recipient_list = db.query(RecipientList).filter(RecipientList.id == list_id).first()
    if not recipient_list:
        raise HTTPException(status_code=404, detail="List not found")
    
    check_list_ownership(recipient_list, current_user)
    
    duplicates = db.query(Recipient).filter(
        Recipient.list_id == list_id,
        Recipient.status == RecipientStatus.NEEDS_REVIEW,
        Recipient.is_duplicate == True
    ).all()
    
    result = []
    for dup in duplicates:
        original = None
        if dup.duplicate_of_id:
            original = db.query(Recipient).filter(Recipient.id == dup.duplicate_of_id).first()
        
        result.append({
            "duplicate": {
                "id": dup.id,
                "email": dup.email,
                "first_name": dup.first_name,
                "last_name": dup.last_name,
                "company": dup.company
            },
            "original": {
                "id": original.id if original else None,
                "email": original.email if original else None,
                "first_name": original.first_name if original else None,
                "last_name": original.last_name if original else None,
                "company": original.company if original else None
            } if original else None
        })
    
    return {
        "list_id": list_id,
        "duplicate_count": len(result),
        "duplicates": result
    }


@router.post("/{list_id}/duplicates/resolve")
def resolve_duplicate(
    list_id: int,
    duplicate_id: int,
    action: str = Query(..., pattern="^(keep|merge|delete)$"),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Resolve a duplicate: keep (activate), merge (update original), or delete"""
    
    # Check list ownership
    recipient_list = db.query(RecipientList).filter(RecipientList.id == list_id).first()
    if not recipient_list:
        raise HTTPException(status_code=404, detail="List not found")
    check_list_ownership(recipient_list, current_user)
    
    duplicate = db.query(Recipient).filter(
        Recipient.id == duplicate_id,
        Recipient.list_id == list_id,
        Recipient.is_duplicate == True
    ).first()
    
    if not duplicate:
        raise HTTPException(status_code=404, detail="Duplicate not found")
    
    if action == "keep":
        # Activate as separate recipient
        duplicate.status = RecipientStatus.ACTIVE
        duplicate.is_duplicate = False
        duplicate.duplicate_of_id = None
        db.commit()
        return {"action": "kept", "id": duplicate_id}
    
    elif action == "merge":
        # Merge into original (update original with any new data)
        if duplicate.duplicate_of_id:
            original = db.query(Recipient).filter(Recipient.id == duplicate.duplicate_of_id).first()
            if original:
                original.first_name = duplicate.first_name or original.first_name
                original.last_name = duplicate.last_name or original.last_name
                original.company = duplicate.company or original.company
                original.title = duplicate.title or original.title
        
        db.delete(duplicate)
        db.commit()
        return {"action": "merged", "merged_into": duplicate.duplicate_of_id}
    
    elif action == "delete":
        db.delete(duplicate)
        db.commit()
        return {"action": "deleted", "id": duplicate_id}


# ==================== Bulk Actions ====================

@router.post("/bulk-action")
def bulk_action(data: RecipientBulkAction, current_user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Perform bulk actions on recipients"""

    recipient_ids = set(data.recipient_ids)
    recipients_query = db.query(Recipient).join(
        RecipientList,
        Recipient.list_id == RecipientList.id,
    ).filter(Recipient.id.in_(recipient_ids))
    if current_user.role != UserRole.ADMIN:
        recipients_query = recipients_query.filter(RecipientList.user_id == current_user.id)
    recipients = recipients_query.all()
    
    if not recipients or len(recipients) != len(recipient_ids):
        raise HTTPException(status_code=404, detail="One or more recipients were not found")
    
    affected = 0
    
    if data.action == "delete":
        for r in recipients:
            db.delete(r)
            affected += 1
    
    elif data.action == "add_tag":
        if not data.tag_id:
            raise HTTPException(status_code=400, detail="tag_id required for add_tag action")
        tag = db.query(RecipientTag).filter(RecipientTag.id == data.tag_id).first()
        if not tag:
            raise HTTPException(status_code=404, detail="Tag not found")
        for r in recipients:
            if tag not in r.tags_rel:
                r.tags_rel.append(tag)
                affected += 1
    
    elif data.action == "remove_tag":
        if not data.tag_id:
            raise HTTPException(status_code=400, detail="tag_id required for remove_tag action")
        tag = db.query(RecipientTag).filter(RecipientTag.id == data.tag_id).first()
        if tag:
            for r in recipients:
                if tag in r.tags_rel:
                    r.tags_rel.remove(tag)
                    affected += 1
    
    elif data.action == "move_to_list":
        if not data.list_id:
            raise HTTPException(status_code=400, detail="list_id required for move_to_list action")
        target_list = db.query(RecipientList).filter(RecipientList.id == data.list_id).first()
        if not target_list:
            raise HTTPException(status_code=404, detail="Target list not found")
        check_list_ownership(target_list, current_user)
        for r in recipients:
            r.list_id = data.list_id
            affected += 1
    
    elif data.action == "change_status":
        if not data.status:
            raise HTTPException(status_code=400, detail="status required for change_status action")
        try:
            status = RecipientStatus(data.status)
            for r in recipients:
                r.status = status
                if status == RecipientStatus.UNSUBSCRIBED:
                    r.is_suppressed = True
                affected += 1
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid status")
    
    db.commit()
    
    return {
        "action": data.action,
        "requested": len(data.recipient_ids),
        "affected": affected
    }
