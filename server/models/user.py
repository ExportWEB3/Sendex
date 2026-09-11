"""
User and Session Models for Authentication
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
import secrets

from database import Base


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"


class ActivationCode(Base):
    """Activation codes for user registration"""
    __tablename__ = "activation_codes"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(32), unique=True, nullable=False, index=True)  # 32-char random string
    
    # Duration in days (how long user account stays active after using this code)
    duration_days = Column(Integer, nullable=False, default=30)
    
    # Usage tracking
    is_used = Column(Boolean, default=False)
    used_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    used_at = Column(DateTime(timezone=True), nullable=True)
    
    # Code validity (when this code expires and can no longer be used)
    expires_at = Column(DateTime(timezone=True), nullable=True)  # None = never expires
    
    # Admin notes
    note = Column(String(500), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    
    # Relationships
    used_by = relationship("User", foreign_keys=[used_by_user_id], backref="used_activation_code")
    created_by = relationship("User", foreign_keys=[created_by_user_id], backref="created_activation_codes")


class User(Base):
    """User account model"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(255), nullable=True)
    role = Column(Enum(UserRole), default=UserRole.USER, nullable=False)
    
    # Account activation (replaces email verification)
    is_active = Column(Boolean, default=False)  # Activated via activation code
    activation_code_id = Column(Integer, ForeignKey("activation_codes.id", ondelete="SET NULL"), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    account_expires_at = Column(DateTime(timezone=True), nullable=True)  # Based on activation code duration
    
    # Password reset
    reset_token = Column(String(255), nullable=True)
    reset_token_expires = Column(DateTime(timezone=True), nullable=True)
    
    # Activity tracking
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")


class UserSession(Base):
    """Track active user sessions (max 2 per user)"""
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # Session token (stored as hash, actual token sent to client)
    token_hash = Column(String(255), nullable=False, unique=True, index=True)
    
    # Device info for identifying sessions
    device_type = Column(String(50), nullable=True)  # 'desktop', 'mobile', 'tablet'
    device_name = Column(String(255), nullable=True)  # Browser/OS info
    ip_address = Column(String(45), nullable=True)
    
    # Session management
    is_active = Column(Boolean, default=True)
    last_activity = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    user = relationship("User", back_populates="sessions")


def generate_token(length: int = 64) -> str:
    """Generate a secure random token"""
    return secrets.token_urlsafe(length)


def generate_activation_code() -> str:
    """Generate a 32-character activation code (alphanumeric, uppercase)"""
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    return ''.join(secrets.choice(alphabet) for _ in range(32))
