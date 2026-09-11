from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime
from models.inbox import InboxState, InboxGroup, SendingMode


class InboxBase(BaseModel):
    email: str
    smtp_account_id: int
    imap_host: Optional[str] = None
    imap_port: int = 993
    imap_username: Optional[str] = None
    imap_password: Optional[str] = None
    reply_to_email: Optional[str] = None
    group: InboxGroup = InboxGroup.A


class InboxCreate(InboxBase):
    reply_enabled: Optional[bool] = None  # Auto-set based on IMAP presence if not provided


class InboxUpdate(BaseModel):
    email: Optional[str] = None
    smtp_account_id: Optional[int] = None
    imap_host: Optional[str] = None
    imap_port: Optional[int] = None
    imap_username: Optional[str] = None
    imap_password: Optional[str] = None
    reply_to_email: Optional[str] = None
    group: Optional[InboxGroup] = None
    state: Optional[InboxState] = None
    daily_cap: Optional[int] = None
    is_active: Optional[bool] = None
    reply_enabled: Optional[bool] = None


class InboxResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    smtp_account_id: int
    imap_host: Optional[str]
    imap_port: int
    group: InboxGroup
    state: InboxState
    warmup_start_date: Optional[datetime]
    warmup_day: int
    daily_cap: int
    current_daily_count: int
    total_sent: int
    total_received: int
    total_replied: int
    bounce_rate: int
    spam_rate: int
    open_rate: int
    reply_rate: int
    is_active: bool
    reply_enabled: bool
    reply_to_email: Optional[str]
    has_imap: bool
    last_sent_at: Optional[datetime]
    last_received_at: Optional[datetime]
    pause_reason: Optional[str]
    sending_mode: Optional[SendingMode] = SendingMode.OFFLINE
    health_score: Optional[float] = 0.0
    last_mode_change: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime]

