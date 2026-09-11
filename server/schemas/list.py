"""
Day 7: List & Tag Schemas
"""

from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime


# ==================== List Schemas ====================

class RecipientListCreate(BaseModel):
    name: str
    description: Optional[str] = None


class RecipientListUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class RecipientListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str]
    recipient_count: int
    active_count: int
    unsubscribed_count: int
    bounced_count: int
    is_active: bool
    created_at: datetime

# ==================== Tag Schemas ====================

class RecipientTagCreate(BaseModel):
    name: str
    color: Optional[str] = "#3B82F6"
    description: Optional[str] = None


class RecipientTagUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    description: Optional[str] = None


class RecipientTagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    color: str
    description: Optional[str]
    recipient_count: int
    created_at: datetime

# ==================== Import Schemas ====================

class ImportRequest(BaseModel):
    handle_duplicates: str = "flag"  # flag, skip, update
    tag_ids: Optional[List[int]] = None


class ImportResponse(BaseModel):
    total: int
    imported: int
    duplicates: int
    updated: int
    skipped: int
    errors: List[str]


# ==================== Recipient Schemas (Updated) ====================

class RecipientCreate(BaseModel):
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None
    list_id: Optional[int] = None
    tag_ids: Optional[List[int]] = None


class RecipientUpdate(BaseModel):
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None
    list_id: Optional[int] = None
    status: Optional[str] = None


class RecipientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    first_name: Optional[str]
    last_name: Optional[str]
    company: Optional[str]
    title: Optional[str]
    list_id: Optional[int]
    status: str
    is_suppressed: bool
    is_duplicate: bool
    total_sent: int
    total_opened: int
    total_replied: int
    created_at: datetime

class RecipientBulkAction(BaseModel):
    recipient_ids: List[int]
    action: str  # delete, add_tag, remove_tag, move_to_list, change_status
    tag_id: Optional[int] = None
    list_id: Optional[int] = None
    status: Optional[str] = None
