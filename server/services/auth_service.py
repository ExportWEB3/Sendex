"""
Authentication Service
Handles user registration, login, activation codes, sessions
"""

import hashlib
import hmac
import secrets
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple, List
from sqlalchemy.orm import Session
from sqlalchemy import and_

from models.user import User, UserSession, UserRole, ActivationCode, generate_token, generate_activation_code


def utc_now():
    """Get current UTC time as timezone-aware datetime"""
    return datetime.now(timezone.utc)


# Password hashing uses a versioned, memory-hard scrypt format. Legacy salted
# SHA-256 hashes remain readable so active accounts can migrate on next login.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64


def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    """Hash password with salt, returns (hash, salt)"""
    if salt is None:
        salt = secrets.token_hex(32)
    derived_key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=bytes.fromhex(salt),
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    ).hex()
    encoded = f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt}${derived_key}"
    return encoded, salt


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against stored hash"""
    try:
        if stored_hash.startswith("scrypt$"):
            algorithm, n, r, p, salt, expected = stored_hash.split('$', 5)
            if algorithm != "scrypt":
                return False
            actual = hashlib.scrypt(
                password.encode("utf-8"),
                salt=bytes.fromhex(salt),
                n=int(n),
                r=int(r),
                p=int(p),
                dklen=len(bytes.fromhex(expected)),
            ).hex()
            return hmac.compare_digest(actual, expected)

        # Compatibility path for hashes created before the scrypt migration.
        salt, hash_value = stored_hash.split('$', 1)
        legacy_hash = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        return hmac.compare_digest(legacy_hash, hash_value)
    except (TypeError, ValueError):
        return False


def hash_token(token: str) -> str:
    """Hash session token for storage"""
    return hashlib.sha256(token.encode()).hexdigest()


class AuthService:
    """Authentication service"""
    
    MAX_SESSIONS_PER_USER = 2
    SESSION_EXPIRY_DAYS = 30
    
    def __init__(self, db: Session):
        self.db = db
    
    # =========================================================================
    # Activation Code Management (Admin)
    # =========================================================================
    
    def create_activation_code(
        self,
        duration_days: int = 30,
        expires_in_days: Optional[int] = None,
        note: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
        count: int = 1
    ) -> List[Dict[str, Any]]:
        """
        Create activation code(s)
        - duration_days: How long the user account stays active after using the code
        - expires_in_days: How long the code itself is valid (None = never expires)
        - note: Admin note for the code
        - count: Number of codes to generate
        """
        codes = []
        
        for _ in range(count):
            code = generate_activation_code()
            
            # Ensure unique code
            while self.db.query(ActivationCode).filter(ActivationCode.code == code).first():
                code = generate_activation_code()
            
            activation = ActivationCode(
                code=code,
                duration_days=duration_days,
                expires_at=utc_now() + timedelta(days=expires_in_days) if expires_in_days else None,
                note=note,
                created_by_user_id=created_by_user_id
            )
            
            self.db.add(activation)
            codes.append({
                "code": code,
                "duration_days": duration_days,
                "expires_at": activation.expires_at.isoformat() if activation.expires_at else None,
                "note": note
            })
        
        self.db.commit()
        return codes
    
    def list_activation_codes(
        self,
        include_used: bool = False,
        include_expired: bool = False
    ) -> List[Dict[str, Any]]:
        """List activation codes"""
        query = self.db.query(ActivationCode)
        
        if not include_used:
            query = query.filter(ActivationCode.is_used == False)
        
        if not include_expired:
            query = query.filter(
                (ActivationCode.expires_at == None) | 
                (ActivationCode.expires_at > utc_now())
            )
        
        codes = query.order_by(ActivationCode.created_at.desc()).all()
        
        result = []
        for c in codes:
            # Get user email if code was used
            used_by_email = None
            if c.used_by_user_id:
                user = self.db.query(User).filter(User.id == c.used_by_user_id).first()
                if user:
                    used_by_email = user.email
            
            result.append({
                "id": c.id,
                "code": c.code,
                "duration_days": c.duration_days,
                "is_used": c.is_used,
                "used_by_user_id": c.used_by_user_id,
                "used_by_email": used_by_email,
                "used_at": c.used_at.isoformat() if c.used_at else None,
                "expires_at": c.expires_at.isoformat() if c.expires_at else None,
                "is_expired": c.expires_at and c.expires_at < utc_now(),
                "note": c.note,
                "created_at": c.created_at.isoformat() if c.created_at else None
            })
        
        return result
    
    def get_activation_code(self, code: str) -> Optional[ActivationCode]:
        """Get activation code by code string"""
        return self.db.query(ActivationCode).filter(
            ActivationCode.code == code.upper().strip()
        ).first()
    
    def delete_activation_code(self, code_id: int) -> Dict[str, Any]:
        """Delete an unused activation code"""
        code = self.db.query(ActivationCode).filter(ActivationCode.id == code_id).first()
        
        if not code:
            return {"success": False, "error": "Activation code not found"}
        
        if code.is_used:
            return {"success": False, "error": "Cannot delete used activation code"}
        
        self.db.delete(code)
        self.db.commit()
        
        return {"success": True, "message": "Activation code deleted"}
    
    def validate_activation_code(self, code: str) -> Dict[str, Any]:
        """Validate an activation code without using it"""
        activation = self.get_activation_code(code)
        
        if not activation:
            return {"valid": False, "error": "Invalid activation code"}
        
        if activation.is_used:
            return {"valid": False, "error": "Activation code already used"}
        
        if activation.expires_at and activation.expires_at < utc_now():
            return {"valid": False, "error": "Activation code has expired"}
        
        return {
            "valid": True,
            "duration_days": activation.duration_days,
            "expires_at": activation.expires_at.isoformat() if activation.expires_at else None
        }
    
    # =========================================================================
    # User Registration with Activation Code
    # =========================================================================
    
    def register_user(
        self,
        email: str,
        password: str,
        activation_code: str,
        name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Register a new user with activation code
        """
        # Validate activation code first
        activation = self.get_activation_code(activation_code)
        
        if not activation:
            return {"success": False, "error": "Invalid activation code"}
        
        if activation.is_used:
            return {"success": False, "error": "Activation code already used"}
        
        if activation.expires_at and activation.expires_at < utc_now():
            return {"success": False, "error": "Activation code has expired"}
        
        # Check if email already exists
        existing = self.db.query(User).filter(User.email == email.lower()).first()
        if existing:
            return {"success": False, "error": "Email already registered"}
        
        # Validate password
        if len(password) < 8:
            return {"success": False, "error": "Password must be at least 8 characters"}
        
        # Create user
        password_hash, _ = hash_password(password)
        
        user = User(
            email=email.lower(),
            password_hash=password_hash,
            name=name,
            role=UserRole.USER,
            is_active=True,  # Active immediately with valid code
            activation_code_id=activation.id,
            activated_at=utc_now(),
            account_expires_at=utc_now() + timedelta(days=activation.duration_days)
        )
        
        self.db.add(user)
        self.db.flush()  # Get user ID
        
        # Mark activation code as used
        activation.is_used = True
        activation.used_by_user_id = user.id
        activation.used_at = utc_now()
        
        self.db.commit()
        self.db.refresh(user)
        
        return {
            "success": True,
            "user": {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "role": user.role.value,
                "account_expires_at": user.account_expires_at.isoformat() if user.account_expires_at else None
            },
            "message": f"Account created! Valid for {activation.duration_days} days."
        }
    
    def create_admin_user(
        self,
        email: str,
        password: str,
        name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create admin user (no activation code required)
        Used for initial setup only
        """
        # Check if email already exists
        existing = self.db.query(User).filter(User.email == email.lower()).first()
        if existing:
            return {"success": False, "error": "Email already registered"}
        
        # Validate password
        if len(password) < 8:
            return {"success": False, "error": "Password must be at least 8 characters"}
        
        # Create admin user
        password_hash, _ = hash_password(password)
        
        user = User(
            email=email.lower(),
            password_hash=password_hash,
            name=name,
            role=UserRole.ADMIN,
            is_active=True,
            activated_at=utc_now(),
            account_expires_at=None  # Admin never expires
        )
        
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        
        return {
            "success": True,
            "user": {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "role": user.role.value
            }
        }
    
    # =========================================================================
    # Login & Sessions
    # =========================================================================
    
    def login(
        self,
        email: str,
        password: str,
        device_type: Optional[str] = None,
        device_name: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Authenticate user and create session
        """
        user = self.db.query(User).filter(User.email == email.lower()).first()
        
        if not user:
            return {"success": False, "error": "Invalid email or password"}
        
        if not verify_password(password, user.password_hash):
            return {"success": False, "error": "Invalid email or password"}

        # Transparently replace legacy salted SHA-256 hashes after a valid login.
        if not user.password_hash.startswith("scrypt$"):
            user.password_hash, _ = hash_password(password)
        
        if not user.is_active:
            return {"success": False, "error": "Account is not active"}
        
        # Check if account expired (skip for admin)
        if user.role != UserRole.ADMIN and user.account_expires_at:
            if user.account_expires_at < utc_now():
                return {"success": False, "error": "Account has expired. Please contact admin for renewal."}
        
        # Create session
        token, session = self._create_session(user, device_type, device_name, ip_address)
        
        # Update last login
        user.last_login_at = utc_now()
        self.db.commit()
        
        return {
            "success": True,
            "token": token,
            "user": {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "role": user.role.value,
                "account_expires_at": user.account_expires_at.isoformat() if user.account_expires_at else None
            }
        }
    
    def _create_session(
        self,
        user: User,
        device_type: Optional[str] = None,
        device_name: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> Tuple[str, UserSession]:
        """Create a new session, enforcing max sessions limit"""
        
        # Get active sessions
        active_sessions = self.db.query(UserSession).filter(
            and_(
                UserSession.user_id == user.id,
                UserSession.is_active == True,
                UserSession.expires_at > utc_now()
            )
        ).order_by(UserSession.last_activity.asc()).all()
        
        # If at max, remove oldest session
        while len(active_sessions) >= self.MAX_SESSIONS_PER_USER:
            oldest = active_sessions.pop(0)
            oldest.is_active = False
            self.db.commit()
        
        # Generate token
        token = generate_token()
        token_hash = hash_token(token)
        
        # Create session
        session = UserSession(
            user_id=user.id,
            token_hash=token_hash,
            device_type=device_type,
            device_name=device_name,
            ip_address=ip_address,
            expires_at=utc_now() + timedelta(days=self.SESSION_EXPIRY_DAYS)
        )
        
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        
        return token, session
    
    def validate_session(self, token: str) -> Optional[User]:
        """Validate session token, returns user if valid"""
        token_hash = hash_token(token)
        
        session = self.db.query(UserSession).filter(
            and_(
                UserSession.token_hash == token_hash,
                UserSession.is_active == True,
                UserSession.expires_at > utc_now()
            )
        ).first()
        
        if not session:
            return None
        
        user = session.user
        
        # Check if account expired (skip for admin)
        if user.role != UserRole.ADMIN and user.account_expires_at:
            if user.account_expires_at < utc_now():
                session.is_active = False
                self.db.commit()
                return None
        
        # Update last activity
        session.last_activity = utc_now()
        self.db.commit()
        
        return user
    
    def logout(self, token: str) -> bool:
        """Invalidate session"""
        token_hash = hash_token(token)
        
        session = self.db.query(UserSession).filter(
            UserSession.token_hash == token_hash
        ).first()
        
        if session:
            session.is_active = False
            self.db.commit()
            return True
        
        return False
    
    def logout_all(self, user_id: int) -> int:
        """Logout all sessions for a user"""
        result = self.db.query(UserSession).filter(
            and_(
                UserSession.user_id == user_id,
                UserSession.is_active == True
            )
        ).update({"is_active": False})
        
        self.db.commit()
        return result
    
    def get_user_sessions(self, user_id: int) -> list:
        """Get all active sessions for a user"""
        sessions = self.db.query(UserSession).filter(
            and_(
                UserSession.user_id == user_id,
                UserSession.is_active == True,
                UserSession.expires_at > utc_now()
            )
        ).all()
        
        return [{
            "id": s.id,
            "device_type": s.device_type,
            "device_name": s.device_name,
            "ip_address": s.ip_address,
            "last_activity": s.last_activity.isoformat() if s.last_activity else None,
            "created_at": s.created_at.isoformat() if s.created_at else None
        } for s in sessions]
    
    # =========================================================================
    # Account Management
    # =========================================================================
    
    def extend_account(self, user_id: int, days: int) -> Dict[str, Any]:
        """Extend user account duration (admin only)"""
        user = self.db.query(User).filter(User.id == user_id).first()
        
        if not user:
            return {"success": False, "error": "User not found"}
        
        if user.account_expires_at:
            # Extend from current expiry or now, whichever is later
            base_time = max(user.account_expires_at, utc_now())
            user.account_expires_at = base_time + timedelta(days=days)
        else:
            user.account_expires_at = utc_now() + timedelta(days=days)
        
        self.db.commit()
        
        return {
            "success": True,
            "message": f"Account extended by {days} days",
            "new_expires_at": user.account_expires_at.isoformat()
        }
    
    def set_account_never_expires(self, user_id: int) -> Dict[str, Any]:
        """Set account to never expire (admin only)"""
        user = self.db.query(User).filter(User.id == user_id).first()
        
        if not user:
            return {"success": False, "error": "User not found"}
        
        user.account_expires_at = None
        self.db.commit()
        
        return {"success": True, "message": "Account set to never expire"}
    
    def change_password(self, user_id: int, old_password: str, new_password: str) -> Dict[str, Any]:
        """Change user password"""
        user = self.db.query(User).filter(User.id == user_id).first()
        
        if not user:
            return {"success": False, "error": "User not found"}
        
        if not verify_password(old_password, user.password_hash):
            return {"success": False, "error": "Current password is incorrect"}
        
        if len(new_password) < 8:
            return {"success": False, "error": "New password must be at least 8 characters"}
        
        password_hash, _ = hash_password(new_password)
        user.password_hash = password_hash
        self.db.commit()
        
        return {"success": True, "message": "Password changed successfully"}
    
    def admin_reset_password(self, user_id: int, new_password: str) -> Dict[str, Any]:
        """Admin reset user password"""
        user = self.db.query(User).filter(User.id == user_id).first()
        
        if not user:
            return {"success": False, "error": "User not found"}
        
        if len(new_password) < 8:
            return {"success": False, "error": "Password must be at least 8 characters"}
        
        password_hash, _ = hash_password(new_password)
        user.password_hash = password_hash
        self.db.commit()
        
        # Logout all sessions
        self.logout_all(user_id)
        
        return {"success": True, "message": "Password reset successfully"}
    
    # =========================================================================
    # Admin Functions
    # =========================================================================
    
    def get_user_by_id(self, user_id: int) -> Optional[User]:
        """Get user by ID"""
        return self.db.query(User).filter(User.id == user_id).first()
    
    def get_user_by_email(self, email: str) -> Optional[User]:
        """Get user by email"""
        return self.db.query(User).filter(User.email == email.lower()).first()
    
    def list_users(self) -> list:
        """List all users (admin only)"""
        users = self.db.query(User).all()
        return [{
            "id": u.id,
            "email": u.email,
            "name": u.name,
            "role": u.role.value,
            "is_active": u.is_active,
            "activated_at": u.activated_at.isoformat() if u.activated_at else None,
            "account_expires_at": u.account_expires_at.isoformat() if u.account_expires_at else None,
            "is_expired": u.account_expires_at and u.account_expires_at < utc_now() if u.role != UserRole.ADMIN else False,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "created_at": u.created_at.isoformat() if u.created_at else None
        } for u in users]
    
    def update_user_role(self, user_id: int, role: UserRole) -> Dict[str, Any]:
        """Update user role (admin only)"""
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            return {"success": False, "error": "User not found"}
        
        user.role = role
        self.db.commit()
        
        return {"success": True, "message": f"User role updated to {role.value}"}
    
    def disable_user(self, user_id: int) -> Dict[str, Any]:
        """Disable user account (admin only)"""
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            return {"success": False, "error": "User not found"}
        
        user.is_active = False
        self.logout_all(user_id)
        self.db.commit()
        
        return {"success": True, "message": "User disabled"}
    
    def enable_user(self, user_id: int) -> Dict[str, Any]:
        """Enable user account (admin only)"""
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            return {"success": False, "error": "User not found"}
        
        user.is_active = True
        self.db.commit()
        
        return {"success": True, "message": "User enabled"}
    
    def delete_user(self, user_id: int) -> Dict[str, Any]:
        """Delete user account (admin only)"""
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            return {"success": False, "error": "User not found"}
        
        if user.role == UserRole.ADMIN:
            # Count admins
            admin_count = self.db.query(User).filter(User.role == UserRole.ADMIN).count()
            if admin_count <= 1:
                return {"success": False, "error": "Cannot delete the last admin user"}
        
        self.db.delete(user)
        self.db.commit()
        
        return {"success": True, "message": "User deleted"}
