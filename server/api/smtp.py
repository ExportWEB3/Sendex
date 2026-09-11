"""
SMTP Account API with multi-tenancy support.
Each user can only access their own SMTP accounts.
Admins can view any user's accounts via ?user_id=X parameter.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime, timezone

from database import get_db
from models.smtp_account import SMTPAccount, ProviderType, EncryptionType
from models.user import User, UserRole
from schemas.smtp import SMTPAccountCreate, SMTPAccountResponse, SMTPAccountUpdate
from services.smtp_service import SMTPService, validate_smtp_credentials, SMTPConnectionError, SMTPSendError
from services.imap_auto_detect import auto_detect_imap, auto_detect_and_save_imap, test_imap_connection
from services.redis_client import get_redis_client
from api.auth import require_auth
import threading
import logging
import os

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/smtp", tags=["SMTP Accounts"])


# Additional schemas for test endpoints
class SMTPTestRequest(BaseModel):
    to_email: str
    subject: str = "Test Email"
    body_text: str = "This is a test email from your FLEETCTRL-X system."


class SMTPValidateRequest(BaseModel):
    host: str
    port: int = 587
    username: str
    password: str = ""
    encryption: str = "tls"
    auth_type: str = "password"
    oauth2_client_id: Optional[str] = None
    oauth2_client_secret: Optional[str] = None
    oauth2_tenant_id: Optional[str] = None


def get_effective_user_id(current_user: User, requested_user_id: Optional[int] = None) -> int:
    """
    Get the effective user_id for queries.
    - Regular users: always their own ID
    - Admins: can specify another user's ID, otherwise their own
    """
    if requested_user_id is not None and current_user.role == UserRole.ADMIN:
        return requested_user_id
    return current_user.id


@router.get("/resend-config")
def get_resend_config(current_user: User = Depends(require_auth)):
    """Get the active Resend sending and shared reply configuration."""
    api_key = os.getenv("RESEND_API_KEY", "")
    verified_domain = (
        os.getenv("RESEND_VERIFIED_DOMAIN", "")
        or os.getenv("BREVO_VERIFIED_DOMAIN", "")
        or os.getenv("SES_VERIFIED_DOMAIN", "")
    )
    imap_host = (
        os.getenv("RESEND_IMAP_HOST", "")
        or os.getenv("BREVO_IMAP_HOST", "")
        or os.getenv("SES_IMAP_HOST", "")
    )
    imap_user = (
        os.getenv("RESEND_IMAP_USER", "")
        or os.getenv("BREVO_IMAP_USER", "")
        or os.getenv("SES_IMAP_USER", "")
    )
    return {
        "configured": bool(api_key),
        "resend_verified_domain": verified_domain,
        "has_api_key": bool(api_key),
        "shared_imap_host": imap_host,
        "shared_imap_user": imap_user,
        "has_shared_imap": bool(imap_host and imap_user),
    }


@router.get("/brevo-config", include_in_schema=False)
def get_legacy_api_config(current_user: User = Depends(require_auth)):
    """Backward-compatible alias for clients deployed before Resend branding."""
    config = get_resend_config(current_user)
    return {
        **config,
        "brevo_verified_domain": config["resend_verified_domain"],
    }


@router.get("/", response_model=List[SMTPAccountResponse])
def get_all_smtp_accounts(
    user_id: Optional[int] = Query(None, description="Admin only: view another user's accounts"),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get all SMTP accounts for current user (or specified user for admins)"""
    effective_user_id = get_effective_user_id(current_user, user_id)
    return db.query(SMTPAccount).filter(SMTPAccount.user_id == effective_user_id).all()


@router.get("/imap-check-schedule")
def get_imap_check_schedule(
    current_user: User = Depends(require_auth),
):
    """Get the next scheduled IMAP check time from worker scheduler"""
    try:
        r = get_redis_client()
        last_check = r.get('scheduler:last_imap_check')
        interval = int(r.get('scheduler:imap_interval') or 300)
        
        if last_check:
            from datetime import datetime, timezone, timedelta
            last_dt = datetime.fromisoformat(last_check)
            next_dt = last_dt + timedelta(seconds=interval)
            now = datetime.now(timezone.utc)
            seconds_until = max(0, int((next_dt - now).total_seconds()))
            return {
                "last_check": last_check,
                "next_check": next_dt.isoformat(),
                "interval_seconds": interval,
                "seconds_until_next": seconds_until,
                "scheduler_active": True,
            }
        return {
            "last_check": None,
            "next_check": None,
            "interval_seconds": 300,
            "seconds_until_next": None,
            "scheduler_active": False,
        }
    except Exception as e:
        return {
            "last_check": None,
            "next_check": None,
            "interval_seconds": 300,
            "seconds_until_next": None,
            "scheduler_active": False,
            "error": str(e),
        }


@router.get("/imap-status/check")
def check_imap_status(
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Check if IMAP is available — via global env config or per-account settings"""
    import os as _os
    from models.inbox import Inbox
    accounts = db.query(SMTPAccount).filter(SMTPAccount.user_id == current_user.id).all()
    
    total = len(accounts)
    with_imap = [a for a in accounts if a.has_imap]
    
    # Also check global env IMAP (shared reply inbox for all users)
    global_imap_host = _os.getenv("RESEND_IMAP_HOST", "") or _os.getenv("BREVO_IMAP_HOST", "") or _os.getenv("SES_IMAP_HOST", "")
    global_imap_user = _os.getenv("RESEND_IMAP_USER", "") or _os.getenv("BREVO_IMAP_USER", "") or _os.getenv("SES_IMAP_USER", "")
    global_imap_pass = _os.getenv("RESEND_IMAP_PASS", "") or _os.getenv("BREVO_IMAP_PASS", "") or _os.getenv("SES_IMAP_PASS", "")
    has_global_imap = bool(global_imap_host and global_imap_user and global_imap_pass)
    
    # Also check if any inbox for this user has IMAP configured
    inbox_with_imap = db.query(Inbox).filter(
        Inbox.user_id == current_user.id,
        Inbox.reply_enabled == True,
        Inbox.imap_host.isnot(None),
        Inbox.imap_username.isnot(None),
    ).count()
    
    imap_active = len(with_imap) > 0 or has_global_imap or inbox_with_imap > 0
    
    return {
        "total_smtp_accounts": total,
        "accounts_with_imap": len(with_imap),
        "global_imap_configured": has_global_imap,
        "global_imap_email": global_imap_user if has_global_imap else None,
        "inboxes_with_imap": inbox_with_imap,
        "imap_active": imap_active,
        "accounts": [
            {"id": a.id, "name": a.name, "has_imap": a.has_imap}
            for a in accounts
        ]
    }


@router.get("/{smtp_id}", response_model=SMTPAccountResponse)
def get_smtp_account(
    smtp_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Get a specific SMTP account by ID"""
    smtp = db.query(SMTPAccount).filter(SMTPAccount.id == smtp_id).first()
    if not smtp:
        raise HTTPException(status_code=404, detail="SMTP account not found")
    
    # Check ownership (admins can view any)
    if smtp.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return smtp


@router.post("/", response_model=SMTPAccountResponse)
def create_smtp_account(
    smtp_data: SMTPAccountCreate,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Create a new SMTP or Resend-backed account with automatic IMAP detection."""
    data = smtp_data.model_dump()
    
    # The historical `brevo` provider value represents API-backed Resend accounts.
    if data.get('provider_type') in (ProviderType.BREVO, 'brevo'):
        if not data.get('host') or data['host'] == '':
            data['host'] = 'resend-api'
        if not data.get('username') or data['username'] == '':
            data['username'] = 'resend'
        if not data.get('password') or data['password'] == '':
            data['password'] = 'resend'
        data['port'] = 443
        data['encryption'] = EncryptionType.TLS
    
    # For OAuth2 accounts, validate required fields and set defaults
    auth_type = data.get('auth_type', 'password')
    if hasattr(auth_type, 'value'):
        auth_type = auth_type.value
    if auth_type == 'oauth2':
        if not data.get('oauth2_client_id') or not data.get('oauth2_client_secret') or not data.get('oauth2_tenant_id'):
            raise HTTPException(status_code=400, detail="OAuth2 accounts require client_id, client_secret, and tenant_id")
        # Default SMTP settings for Microsoft 365
        if not data.get('host') or data['host'] == '':
            data['host'] = 'smtp.office365.com'
        if not data.get('port') or data['port'] == 587:
            data['port'] = 587
        data['encryption'] = EncryptionType.TLS
        if not data.get('password') or data['password'] == '':
            data['password'] = 'oauth2-managed'

    # Legacy API-typed records are also routed through the active Resend engine.
    if data.get('provider_type') in (ProviderType.SES_API, 'ses_api'):
        # Placeholder fields satisfy historical non-null database columns.
        if not data.get('host') or data['host'] == '':
            data['host'] = 'resend-api'
        if not data.get('username') or data['username'] == '':
            data['username'] = 'resend'
        if not data.get('password') or data['password'] == '':
            data['password'] = 'resend'
        data['port'] = 443
        data['encryption'] = EncryptionType.TLS
    
    smtp = SMTPAccount(**data)
    smtp.user_id = current_user.id
    
    # Auto-detect IMAP if not manually provided
    if not smtp.imap_host:
        try:
            detection = auto_detect_imap(
                smtp_host=smtp.host,
                smtp_username=smtp.username,
                smtp_password=smtp.password
            )
            if detection["detected"]:
                smtp.imap_host = detection["imap_host"]
                smtp.imap_port = detection["imap_port"]
                smtp.imap_username = detection["imap_username"]
                smtp.imap_password = detection["imap_password"]
                logger.info(f"Auto-detected IMAP for {smtp.host}: {detection['imap_host']}:{detection['imap_port']}")
        except Exception as e:
            logger.warning(f"IMAP auto-detection failed for {smtp.host}: {e}")
    
    db.add(smtp)
    db.commit()
    db.refresh(smtp)
    return smtp


@router.put("/{smtp_id}", response_model=SMTPAccountResponse)
def update_smtp_account(
    smtp_id: int,
    smtp_data: SMTPAccountUpdate,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Update an existing SMTP account with auto IMAP re-detection"""
    smtp = db.query(SMTPAccount).filter(SMTPAccount.id == smtp_id).first()
    if not smtp:
        raise HTTPException(status_code=404, detail="SMTP account not found")
    
    # Check ownership (admins can edit any)
    if smtp.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    update_data = smtp_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(smtp, field, value)
    
    # If SMTP host/credentials changed and no IMAP set, re-detect
    if ("host" in update_data or "username" in update_data or "password" in update_data) and not smtp.imap_host:
        try:
            detection = auto_detect_imap(
                smtp_host=smtp.host,
                smtp_username=smtp.username,
                smtp_password=smtp.password
            )
            if detection["detected"]:
                smtp.imap_host = detection["imap_host"]
                smtp.imap_port = detection["imap_port"]
                smtp.imap_username = detection["imap_username"]
                smtp.imap_password = detection["imap_password"]
                logger.info(f"Auto-detected IMAP on update for {smtp.host}: {detection['imap_host']}:{detection['imap_port']}")
        except Exception as e:
            logger.warning(f"IMAP auto-detection failed on update: {e}")
    
    db.commit()
    db.refresh(smtp)
    return smtp


@router.delete("/{smtp_id}")
def delete_smtp_account(
    smtp_id: int,
    force: bool = False,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Delete an SMTP account. Use force=true to delete associated inboxes first."""
    from models.inbox import Inbox
    from models.campaign import CampaignRecipient
    from models.warmup import CampaignAutoReply, WarmupPartner, WarmupReply, WarmupThread
    from models.monitoring import DailyMetrics, ReputationHistory, Alert
    from models.email import Email
    
    smtp = db.query(SMTPAccount).filter(SMTPAccount.id == smtp_id).first()
    if not smtp:
        raise HTTPException(status_code=404, detail="SMTP account not found")
    
    # Check ownership (admins can delete any)
    if smtp.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Check for linked inboxes
    linked_inboxes = db.query(Inbox).filter(Inbox.smtp_account_id == smtp_id).all()
    
    if linked_inboxes and not force:
        inbox_names = [i.email for i in linked_inboxes]
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot delete: {len(linked_inboxes)} inbox(es) use this SMTP account ({', '.join(inbox_names[:3])}{'...' if len(inbox_names) > 3 else ''}). Delete inboxes first or use force=true."
        )
    
    if force and linked_inboxes:
        # Clean up all FK references for each inbox first
        # Order matters: campaign_auto_replies references campaign_recipients,
        # so delete auto_replies (by campaign_recipient_id) BEFORE campaign_recipients
        inbox_ids = [i.id for i in linked_inboxes]
        for inbox_id in inbox_ids:
            # Get campaign_recipient IDs for this inbox so we can delete their auto-replies first
            cr_ids = [r[0] for r in db.query(CampaignRecipient.id).filter(CampaignRecipient.inbox_id == inbox_id).all()]
            if cr_ids:
                db.query(CampaignAutoReply).filter(CampaignAutoReply.campaign_recipient_id.in_(cr_ids)).delete(synchronize_session=False)
            # Also delete any auto-replies linked directly by inbox_id
            db.query(CampaignAutoReply).filter(CampaignAutoReply.inbox_id == inbox_id).delete()
            # Now safe to delete campaign recipients
            db.query(CampaignRecipient).filter(CampaignRecipient.inbox_id == inbox_id).delete()
            db.query(WarmupPartner).filter(WarmupPartner.inbox_id == inbox_id).delete()
            db.query(WarmupPartner).filter(WarmupPartner.partner_inbox_id == inbox_id).delete()
            db.query(WarmupReply).filter(WarmupReply.inbox_id == inbox_id).delete()
            db.query(WarmupThread).filter(WarmupThread.sender_inbox_id == inbox_id).delete()
            db.query(WarmupThread).filter(WarmupThread.recipient_inbox_id == inbox_id).delete()
            db.query(WarmupThread).filter(WarmupThread.last_sender_id == inbox_id).delete()
            db.query(DailyMetrics).filter(DailyMetrics.inbox_id == inbox_id).delete()
            db.query(ReputationHistory).filter(ReputationHistory.inbox_id == inbox_id).delete()
            db.query(Alert).filter(Alert.inbox_id == inbox_id).delete()
            db.query(Email).filter(Email.inbox_id == inbox_id).delete()
        for inbox in linked_inboxes:
            db.delete(inbox)
    
    # Clean up emails directly linked to SMTP account
    db.query(Email).filter(Email.smtp_account_id == smtp_id).delete()
    
    # Clean up Redis queues — remove any stale queued emails for this SMTP account
    try:
        import json as _json
        _r = get_redis_client()
        for queue_key in ['email:queue:high', 'email:queue:normal', 'email:queue:low']:
            if _r.exists(queue_key):
                items = _r.zrange(queue_key, 0, -1)
                for item in items:
                    try:
                        data = _json.loads(item)
                        if data.get('smtp_account_id') == smtp_id:
                            _r.zrem(queue_key, item)
                    except Exception:
                        pass
        logger.info(f"Cleaned Redis queues for SMTP account {smtp_id}")
    except Exception as e:
        logger.warning(f"Redis cleanup failed (non-critical): {e}")
    
    db.delete(smtp)
    db.commit()
    return {"success": True, "message": "SMTP account deleted successfully", "inboxes_deleted": len(linked_inboxes) if force else 0}


@router.post("/validate")
def validate_smtp(
    data: SMTPValidateRequest,
    current_user: User = Depends(require_auth)
):
    """Validate SMTP credentials without saving"""
    result = validate_smtp_credentials(
        host=data.host,
        port=data.port,
        username=data.username,
        password=data.password,
        encryption=data.encryption,
        auth_type=data.auth_type,
        oauth2_client_id=data.oauth2_client_id,
        oauth2_client_secret=data.oauth2_client_secret,
        oauth2_tenant_id=data.oauth2_tenant_id,
    )
    if not result["valid"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.post("/{smtp_id}/test")
def test_smtp_account(
    smtp_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Test an SMTP or Resend connection and update its active state."""
    smtp = db.query(SMTPAccount).filter(SMTPAccount.id == smtp_id).first()
    if not smtp:
        raise HTTPException(status_code=404, detail="SMTP account not found")
    
    # Check ownership (admins can test any)
    if smtp.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    try:
        # Branch on provider type
        provider = getattr(smtp, 'provider_type', 'smtp') or 'smtp'
        if provider in ('brevo', 'ses_api'):
            from services.resend_service import get_resend_service
            api_svc = get_resend_service()
            if not api_svc:
                raise HTTPException(status_code=400, detail="Resend API not configured. Set RESEND_API_KEY in .env")
            info = api_svc.get_account_info_sync()
            if info.get('success'):
                smtp.last_used_at = datetime.now(timezone.utc)
                smtp.last_error = None
                smtp.is_active = True
                db.commit()
                return {
                    "success": True,
                    "message": "Resend API connection OK.",
                    "status": "active",
                    "provider": "resend",
                }
            else:
                smtp.last_error = info.get('error', 'API connection failed')
                smtp.is_active = False
                db.commit()
                raise HTTPException(status_code=400, detail=info.get('error', 'API connection failed'))
        else:
            # Regular SMTP test
            result = validate_smtp_credentials(
                host=smtp.host,
                port=smtp.port,
                username=smtp.username,
                password=smtp.password,
                encryption=smtp.encryption,
                auth_type=getattr(smtp, 'auth_type', 'password') or 'password',
                oauth2_client_id=getattr(smtp, 'oauth2_client_id', None),
                oauth2_client_secret=getattr(smtp, 'oauth2_client_secret', None),
                oauth2_tenant_id=getattr(smtp, 'oauth2_tenant_id', None),
            )
            
            if result["valid"]:
                smtp.last_used_at = datetime.now(timezone.utc)
                smtp.last_error = None
                smtp.is_active = True
                db.commit()
                return {
                    "success": True,
                    "message": f"Successfully connected to {smtp.host}:{smtp.port}",
                    "status": "active",
                    "provider": "smtp"
                }
            else:
                smtp.last_error = result["message"]
                smtp.is_active = False
                db.commit()
                raise HTTPException(status_code=400, detail=result["message"])
        
    except HTTPException:
        raise
    except Exception as e:
        smtp.last_error = str(e)
        smtp.is_active = False
        db.commit()
        raise HTTPException(status_code=500, detail=f"Connection error: {str(e)}")


@router.post("/{smtp_id}/send-test")
def send_test_email(
    smtp_id: int,
    test_data: SMTPTestRequest,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Send a test email using an existing SMTP or Resend account."""
    smtp = db.query(SMTPAccount).filter(SMTPAccount.id == smtp_id).first()
    if not smtp:
        raise HTTPException(status_code=404, detail="SMTP account not found")
    
    # Check ownership (admins can use any)
    if smtp.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    from services.kill_switch import KILL_SWITCH_MESSAGE, is_kill_switch_enabled
    if is_kill_switch_enabled():
        raise HTTPException(status_code=403, detail=KILL_SWITCH_MESSAGE)
    
    try:
        provider = getattr(smtp, 'provider_type', 'smtp') or 'smtp'
        if provider in ('brevo', 'ses_api'):
            from services.resend_service import get_resend_service
            api_svc = get_resend_service()
            if not api_svc:
                raise HTTPException(status_code=400, detail="Resend API not configured. Set RESEND_API_KEY in .env")
            result = api_svc.send_email_sync(
                to_email=test_data.to_email,
                to_name=None,
                from_email=smtp.from_email,
                from_name=smtp.from_name or None,
                subject=test_data.subject,
                html_content=f"<html><body><p>{test_data.body_text}</p></body></html>",
                text_content=test_data.body_text,
            )
            if not result.get('success'):
                raise HTTPException(status_code=400, detail=f"Send error: {result.get('error')}")
            
            smtp.last_used_at = datetime.now(timezone.utc)
            smtp.last_error = None
            db.commit()
            
            return {
                "success": True,
                "message": f"Test email sent via Resend API to {test_data.to_email}",
                "provider": "resend",
                "details": {"message_id": result.get('message_id')}
            }
        else:
            service = SMTPService(smtp)
            result = service.send_with_retry(
                to_email=test_data.to_email,
                subject=test_data.subject,
                body_text=test_data.body_text,
                body_html=f"<html><body><p>{test_data.body_text}</p></body></html>"
            )
            service.disconnect()
            
            smtp.last_used_at = datetime.now(timezone.utc)
            smtp.last_error = None
            db.commit()
            
            return {
                "success": True,
                "message": f"Test email sent to {test_data.to_email}",
                "provider": "smtp",
                "details": result
            }
        
    except HTTPException:
        raise
    except SMTPConnectionError as e:
        smtp.last_error = str(e)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Connection error: {str(e)}")
    except SMTPSendError as e:
        smtp.last_error = str(e)
        db.commit()
        raise HTTPException(
            status_code=400 if e.is_permanent else 500,
            detail=f"Send error: {e.message}"
        )


@router.post("/{smtp_id}/detect-imap")
def detect_imap_for_account(
    smtp_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Manually trigger IMAP auto-detection for an existing SMTP account"""
    smtp = db.query(SMTPAccount).filter(SMTPAccount.id == smtp_id).first()
    if not smtp:
        raise HTTPException(status_code=404, detail="SMTP account not found")
    if smtp.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    detection = auto_detect_and_save_imap(smtp, db)
    
    return {
        "success": detection["detected"],
        "imap_host": detection.get("imap_host"),
        "imap_port": detection.get("imap_port"),
        "method": detection.get("method"),
        "message": detection.get("message"),
        "inboxes_updated": detection.get("inboxes_updated", 0)
    }


@router.post("/{smtp_id}/test-imap")
def test_imap_for_account(
    smtp_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Test IMAP connection for an SMTP account"""
    smtp = db.query(SMTPAccount).filter(SMTPAccount.id == smtp_id).first()
    if not smtp:
        raise HTTPException(status_code=404, detail="SMTP account not found")
    if smtp.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    if not smtp.has_imap:
        raise HTTPException(status_code=400, detail="No IMAP settings configured for this account")
    
    result = test_imap_connection(
        host=smtp.imap_host,
        port=smtp.imap_port,
        username=smtp.imap_username,
        password=smtp.imap_password
    )
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    
    return {
        "success": True,
        "message": result["message"],
        "host": result["host"],
        "port": result["port"],
        "inbox_accessible": result.get("inbox_accessible", False)
    }


