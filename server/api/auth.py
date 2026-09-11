"""
Authentication API Endpoints
"""

from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional, List
from sqlalchemy.orm import Session

from database import get_db
from models.user import User, UserRole
from services.auth_service import AuthService, hash_password
from services.password_reset_service import (
    PasswordResetRateLimited,
    PasswordResetUnavailable,
    issue_password_reset_challenge,
    send_password_reset_code,
    verify_and_consume_password_reset_challenge,
)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

security = HTTPBearer(auto_error=False)


# =============================================================================
# Pydantic Schemas
# =============================================================================

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    activation_code: str
    name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ValidateCodeRequest(BaseModel):
    code: str


class CreateActivationCodeRequest(BaseModel):
    duration_days: int = 30  # How long user account is valid
    expires_in_days: Optional[int] = None  # When code expires (None = never)
    note: Optional[str] = None
    count: int = 1  # Number of codes to generate


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class UpdateRoleRequest(BaseModel):
    role: str


class AdminResetPasswordRequest(BaseModel):
    new_password: str


class ExtendAccountRequest(BaseModel):
    days: int


class ForgotPasswordCaptchaRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResetRequest(BaseModel):
    email: EmailStr
    captcha_id: str
    captcha_answer: str
    new_password: str
    confirm_password: str

    @field_validator('new_password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        if not any(c.isupper() for c in v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not any(c.islower() for c in v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not any(c.isdigit() for c in v):
            raise ValueError('Password must contain at least one number')
        return v


# =============================================================================
# Auth Dependencies
# =============================================================================

def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """Get current user from token, returns None if not authenticated"""
    if not credentials:
        return None
    
    auth_service = AuthService(db)
    user = auth_service.validate_session(credentials.credentials)
    return user


def require_auth(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
    db: Session = Depends(get_db)
) -> User:
    """Require authentication, raises 401 if not authenticated"""
    auth_service = AuthService(db)
    user = auth_service.validate_session(credentials.credentials)
    
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account is disabled")
    
    return user


def require_admin(user: User = Depends(require_auth)) -> User:
    """Require admin role"""
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# =============================================================================
# Helper Functions
# =============================================================================

def get_client_info(request: Request) -> dict:
    """Extract client info from request"""
    user_agent = request.headers.get("user-agent", "")
    
    # Simple device detection
    device_type = "desktop"
    if "Mobile" in user_agent or "Android" in user_agent:
        device_type = "mobile"
    elif "Tablet" in user_agent or "iPad" in user_agent:
        device_type = "tablet"
    
    # Get IP
    ip_address = request.client.host if request.client else None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        ip_address = forwarded.split(",")[0].strip()
    
    return {
        "device_type": device_type,
        "device_name": user_agent[:255] if user_agent else None,
        "ip_address": ip_address
    }


# =============================================================================
# Public Endpoints
# =============================================================================

@router.post("/validate-code")
def validate_code(request: ValidateCodeRequest, db: Session = Depends(get_db)):
    """Validate an activation code before registration"""
    auth_service = AuthService(db)
    result = auth_service.validate_activation_code(request.code)
    
    if not result.get("valid"):
        raise HTTPException(status_code=400, detail=result.get("error", "Invalid code"))
    
    return result


@router.post("/register")
def register(request: RegisterRequest, db: Session = Depends(get_db)):
    """Register a new user account with activation code"""
    auth_service = AuthService(db)
    
    result = auth_service.register_user(
        email=request.email,
        password=request.password,
        activation_code=request.activation_code,
        name=request.name
    )
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    
    return result


@router.post("/login")
def login(request: LoginRequest, req: Request, db: Session = Depends(get_db)):
    """Login and get session token"""
    auth_service = AuthService(db)
    client_info = get_client_info(req)
    
    result = auth_service.login(
        email=request.email,
        password=request.password,
        **client_info
    )
    
    if not result["success"]:
        raise HTTPException(status_code=401, detail=result["error"])
    
    return {
        "success": True,
        "token": result["token"],
        "user": result["user"]
    }


@router.post("/logout")
def logout(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
    db: Session = Depends(get_db)
):
    """Logout current session"""
    auth_service = AuthService(db)
    auth_service.logout(credentials.credentials)
    return {"success": True, "message": "Logged out successfully"}


# =============================================================================
# Authenticated Endpoints
# =============================================================================

@router.get("/me")
def get_me(user: User = Depends(require_auth)):
    """Get current user info"""
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role.value,
        "is_active": user.is_active,
        "activated_at": user.activated_at.isoformat() if user.activated_at else None,
        "account_expires_at": user.account_expires_at.isoformat() if user.account_expires_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None
    }


@router.get("/sessions")
def get_sessions(user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Get current user's active sessions"""
    auth_service = AuthService(db)
    sessions = auth_service.get_user_sessions(user.id)
    return {"sessions": sessions, "max_sessions": auth_service.MAX_SESSIONS_PER_USER}


@router.post("/logout-all")
def logout_all_sessions(user: User = Depends(require_auth), db: Session = Depends(get_db)):
    """Logout all sessions for current user"""
    auth_service = AuthService(db)
    count = auth_service.logout_all(user.id)
    return {"success": True, "message": f"Logged out {count} session(s)"}


@router.post("/change-password")
def change_password(
    request: ChangePasswordRequest,
    user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Change current user's password"""
    auth_service = AuthService(db)
    result = auth_service.change_password(user.id, request.old_password, request.new_password)
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    
    return result


# =============================================================================
# Admin - Activation Code Management
# =============================================================================

@router.post("/activation-codes", dependencies=[Depends(require_admin)])
def create_activation_codes(
    request: CreateActivationCodeRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Create new activation code(s) (admin only)"""
    auth_service = AuthService(db)
    
    codes = auth_service.create_activation_code(
        duration_days=request.duration_days,
        expires_in_days=request.expires_in_days,
        note=request.note,
        created_by_user_id=admin.id,
        count=request.count
    )
    
    return {
        "success": True,
        "codes": codes,
        "message": f"Created {len(codes)} activation code(s)"
    }


@router.get("/activation-codes", dependencies=[Depends(require_admin)])
def list_activation_codes(
    include_used: bool = False,
    include_expired: bool = False,
    db: Session = Depends(get_db)
):
    """List activation codes (admin only)"""
    auth_service = AuthService(db)
    codes = auth_service.list_activation_codes(
        include_used=include_used,
        include_expired=include_expired
    )
    return {"codes": codes}


@router.delete("/activation-codes/{code_id}", dependencies=[Depends(require_admin)])
def delete_activation_code(code_id: int, db: Session = Depends(get_db)):
    """Delete an unused activation code (admin only)"""
    auth_service = AuthService(db)
    result = auth_service.delete_activation_code(code_id)
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    
    return result


# =============================================================================
# Admin - User Management
# =============================================================================

@router.get("/users", dependencies=[Depends(require_admin)])
def list_users(db: Session = Depends(get_db)):
    """List all users (admin only)"""
    auth_service = AuthService(db)
    users = auth_service.list_users()
    return {"users": users}


@router.post("/users/{user_id}/disable", dependencies=[Depends(require_admin)])
def disable_user(user_id: int, db: Session = Depends(get_db)):
    """Disable a user account (admin only)"""
    auth_service = AuthService(db)
    result = auth_service.disable_user(user_id)
    
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["error"])
    
    return result


@router.post("/users/{user_id}/enable", dependencies=[Depends(require_admin)])
def enable_user(user_id: int, db: Session = Depends(get_db)):
    """Enable a user account (admin only)"""
    auth_service = AuthService(db)
    result = auth_service.enable_user(user_id)
    
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["error"])
    
    return result


@router.post("/users/{user_id}/extend", dependencies=[Depends(require_admin)])
def extend_account(
    user_id: int,
    request: ExtendAccountRequest,
    db: Session = Depends(get_db)
):
    """Extend user account duration (admin only)"""
    auth_service = AuthService(db)
    result = auth_service.extend_account(user_id, request.days)
    
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["error"])
    
    return result


@router.post("/users/{user_id}/never-expires", dependencies=[Depends(require_admin)])
def set_never_expires(user_id: int, db: Session = Depends(get_db)):
    """Set user account to never expire (admin only)"""
    auth_service = AuthService(db)
    result = auth_service.set_account_never_expires(user_id)
    
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["error"])
    
    return result


@router.post("/users/{user_id}/reset-password", dependencies=[Depends(require_admin)])
def admin_reset_password(
    user_id: int,
    request: AdminResetPasswordRequest,
    db: Session = Depends(get_db)
):
    """Reset user password (admin only)"""
    auth_service = AuthService(db)
    result = auth_service.admin_reset_password(user_id, request.new_password)
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    
    return result


@router.put("/users/{user_id}/role")
def update_user_role(
    user_id: int,
    request: UpdateRoleRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Update user role (admin only)"""
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot change your own role")
    
    try:
        user_role = UserRole(request.role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role: {request.role}")
    
    auth_service = AuthService(db)
    result = auth_service.update_user_role(user_id, user_role)
    
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["error"])
    
    return result


@router.get("/users/{user_id}/details", dependencies=[Depends(require_admin)])
def get_user_details(user_id: int, db: Session = Depends(get_db)):
    """Get detailed user info including their resources (admin only)"""
    from models.campaign import Campaign
    from models.smtp_account import SMTPAccount
    from models.inbox import Inbox
    from models.list import RecipientList
    
    # Get user
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get user's resources
    campaigns = db.query(Campaign).filter(Campaign.user_id == user_id).all()
    smtp_accounts = db.query(SMTPAccount).filter(SMTPAccount.user_id == user_id).all()
    inboxes = db.query(Inbox).filter(Inbox.user_id == user_id).all()
    lists = db.query(RecipientList).filter(RecipientList.user_id == user_id).all()
    
    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role.value if hasattr(user.role, 'value') else user.role,
            "is_active": user.is_active,
            "account_expires_at": user.account_expires_at.isoformat() if user.account_expires_at else None,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            "activated_at": user.activated_at.isoformat() if user.activated_at else None,
        },
        "stats": {
            "campaigns": len(campaigns),
            "smtp_accounts": len(smtp_accounts),
            "inboxes": len(inboxes),
            "lists": len(lists),
            "total_recipients": sum(l.recipient_count or 0 for l in lists),
        },
        "campaigns": [
            {
                "id": c.id,
                "name": c.name,
                "status": c.status.value if hasattr(c.status, 'value') else c.status,
                "total_recipients": c.total_recipients,
                "total_sent": c.total_sent,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in campaigns
        ],
        "smtp_accounts": [
            {
                "id": s.id,
                "name": s.name,
                "host": s.host,
                "from_email": s.from_email,
                "is_active": s.is_active,
            }
            for s in smtp_accounts
        ],
        "inboxes": [
            {
                "id": i.id,
                "email": i.email,
                "state": i.state.value if hasattr(i.state, 'value') else i.state,
                "is_active": i.is_active,
                "warmup_day": i.warmup_day,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in inboxes
        ],
        "lists": [
            {
                "id": l.id,
                "name": l.name,
                "recipient_count": l.recipient_count,
            }
            for l in lists
        ],
    }


@router.delete("/users/{user_id}", dependencies=[Depends(require_admin)])
def delete_user(user_id: int, db: Session = Depends(get_db)):
    """Delete a user (admin only)"""
    auth_service = AuthService(db)
    result = auth_service.delete_user(user_id)
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    
    return result


# =============================================================================
# Forgot Password with Email Verification
# =============================================================================


@router.post("/forgot-password/captcha")
def get_forgot_password_captcha(
    request: ForgotPasswordCaptchaRequest,
    req: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Step 1: issue a one-time email verification challenge.
    The response remains identical for unknown addresses to prevent enumeration.
    """
    try:
        challenge = issue_password_reset_challenge(
            request.email,
            req.client.host if req.client else "unknown",
        )
    except PasswordResetRateLimited as exc:
        raise HTTPException(status_code=429, detail="Too many reset requests. Please try again later.") from exc
    except PasswordResetUnavailable as exc:
        raise HTTPException(status_code=503, detail="Password reset is temporarily unavailable.") from exc

    auth_service = AuthService(db)
    if auth_service.get_user_by_email(request.email):
        background_tasks.add_task(send_password_reset_code, request.email, challenge.code)
    
    return {
        "success": True,
        "captcha_id": challenge.challenge_id,
        "captcha_question": "Enter the 6-digit verification code sent to your email",
    }


@router.post("/forgot-password/reset")
def reset_password_with_captcha(request: ForgotPasswordResetRequest, db: Session = Depends(get_db)):
    """
    Step 2: verify the emailed code and set the new password.
    """
    # Validate passwords match
    if request.new_password != request.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")

    try:
        status, remaining = verify_and_consume_password_reset_challenge(
            request.captcha_id,
            request.email,
            request.captcha_answer,
        )
    except PasswordResetUnavailable as exc:
        raise HTTPException(status_code=503, detail="Password reset is temporarily unavailable.") from exc

    if status == "missing":
        raise HTTPException(status_code=400, detail="Verification code expired or invalid. Please try again.")
    if status == "too_many":
        raise HTTPException(status_code=429, detail="Too many attempts. Please request a new code.")
    if status == "wrong":
        raise HTTPException(status_code=400, detail=f"Wrong verification code. {remaining} attempt(s) remaining.")
    if status == "email_mismatch":
        raise HTTPException(status_code=400, detail="Email mismatch. Please start over.")
    if status != "ok":
        raise HTTPException(status_code=400, detail="Verification code expired or invalid. Please try again.")
    
    # Find user
    auth_service = AuthService(db)
    user = auth_service.get_user_by_email(request.email)
    
    if not user:
        # Don't reveal that email doesn't exist
        raise HTTPException(status_code=400, detail="Unable to reset password. Please check your email and try again.")
    
    # Reset password
    new_hash, _ = hash_password(request.new_password)
    user.password_hash = new_hash
    db.commit()
    
    # Logout all existing sessions
    auth_service.logout_all(user.id)
    
    return {
        "success": True,
        "message": "Password reset successfully! You can now sign in with your new password."
    }
