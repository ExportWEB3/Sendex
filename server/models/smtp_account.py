from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from database import Base


class EncryptionType(str, enum.Enum):
    NONE = "none"
    SSL = "ssl"
    TLS = "tls"


class AuthType(str, enum.Enum):
    PASSWORD = "password"
    OAUTH2 = "oauth2"


class ProviderType(str, enum.Enum):
    SMTP = "smtp"
    BREVO = "brevo"
    SES_API = "ses_api"
    SES_SMTP = "ses_smtp"


class SMTPAccount(Base):
    __tablename__ = "smtp_accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # Owner
    name = Column(String(255), nullable=False)  # server friendly name
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False, default=587)
    username = Column(String(255), nullable=False)
    password = Column(String(255), nullable=False)  # Will encrypt later
    encryption = Column(Enum(EncryptionType), default=EncryptionType.TLS)
    from_email = Column(String(255), nullable=False)
    from_name = Column(String(255), nullable=True)
    
    # Rate limiting
    hourly_limit = Column(Integer, default=100)
    daily_limit = Column(Integer, default=1000)
    current_hourly_count = Column(Integer, default=0)
    current_daily_count = Column(Integer, default=0)
    
    # Status
    is_active = Column(Boolean, default=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(String(500), nullable=True)
    
    # IMAP settings (optional - for reading replies)
    imap_host = Column(String(255), nullable=True)
    imap_port = Column(Integer, nullable=True)
    imap_username = Column(String(255), nullable=True)
    imap_password = Column(String(255), nullable=True)
    
    @property
    def has_imap(self) -> bool:
        """Check if IMAP credentials are configured"""
        return bool(self.imap_host and self.imap_username and self.imap_password)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Provider details (for hybrid provider support)
    provider_type = Column(String(20), default='smtp')
    # Legacy provider fields retained for existing database records.
    aws_access_key_id = Column(String(255), nullable=True)
    aws_secret_access_key = Column(String(255), nullable=True)
    aws_region = Column(String(64), nullable=True)

    # Auth type: password (default) or oauth2 (Microsoft 365 / Outlook)
    auth_type = Column(String(20), default='password')
    # OAuth2 credentials (required when auth_type = oauth2)
    oauth2_client_id = Column(String(255), nullable=True)
    oauth2_client_secret = Column(String(512), nullable=True)
    oauth2_tenant_id = Column(String(255), nullable=True)
