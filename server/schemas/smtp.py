from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime
from models.smtp_account import EncryptionType, ProviderType, AuthType


class SMTPAccountBase(BaseModel):
    name: str
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    encryption: EncryptionType = EncryptionType.TLS
    from_email: str
    from_name: str
    hourly_limit: int = 100
    daily_limit: int = 1000
    imap_host: Optional[str] = None
    imap_port: Optional[int] = None
    imap_username: Optional[str] = None
    imap_password: Optional[str] = None
    # Provider type; historical values remain accepted for existing records.
    provider_type: ProviderType = ProviderType.SMTP
    # Auth type: password (default) or oauth2 (Microsoft 365)
    auth_type: AuthType = AuthType.PASSWORD
    # OAuth2 fields (required when auth_type is oauth2)
    oauth2_client_id: Optional[str] = None
    oauth2_client_secret: Optional[str] = None
    oauth2_tenant_id: Optional[str] = None


class SMTPAccountCreate(SMTPAccountBase):
    pass


class SMTPAccountUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    encryption: Optional[EncryptionType] = None
    from_email: Optional[str] = None
    from_name: Optional[str] = None
    hourly_limit: Optional[int] = None
    daily_limit: Optional[int] = None
    is_active: Optional[bool] = None
    imap_host: Optional[str] = None
    imap_port: Optional[int] = None
    imap_username: Optional[str] = None
    imap_password: Optional[str] = None
    provider_type: Optional[ProviderType] = None
    auth_type: Optional[AuthType] = None
    oauth2_client_id: Optional[str] = None
    oauth2_client_secret: Optional[str] = None
    oauth2_tenant_id: Optional[str] = None


class SMTPAccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    host: str
    port: int
    username: str
    encryption: EncryptionType
    from_email: str
    from_name: Optional[str]
    hourly_limit: int
    daily_limit: int
    current_hourly_count: int
    current_daily_count: int
    is_active: bool
    last_used_at: Optional[datetime]
    last_error: Optional[str]
    imap_host: Optional[str] = None
    imap_port: Optional[int] = None
    imap_username: Optional[str] = None
    has_imap: bool = False
    provider_type: Optional[ProviderType] = ProviderType.SMTP
    auth_type: Optional[str] = 'password'
    oauth2_client_id: Optional[str] = None
    oauth2_tenant_id: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime]

