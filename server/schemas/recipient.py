from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime


class RecipientBase(BaseModel):
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    tags: Optional[str] = None


class RecipientCreate(RecipientBase):
    pass


class RecipientUpdate(BaseModel):
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    tags: Optional[str] = None
    is_suppressed: Optional[bool] = None
    suppression_reason: Optional[str] = None


class RecipientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    first_name: Optional[str]
    last_name: Optional[str]
    company: Optional[str]
    tags: Optional[str]
    is_suppressed: bool
    suppression_reason: Optional[str]
    total_sent: int
    total_opened: int
    total_clicked: int
    total_replied: int
    total_bounced: int
    last_sent_at: Optional[datetime]
    last_opened_at: Optional[datetime]
    last_clicked_at: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]

