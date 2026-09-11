"""
Bulk Import API

POST /api/import/preview   — parse file, return safe preview (no secrets)
POST /api/import/execute   — commit a previously-previewed payload

The execute endpoint receives the exact parsed payload returned by preview
so nothing from the raw file is retained server-side between the two calls.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, ValidationError
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from api.auth import require_auth
from database import get_db
from models.inbox import Inbox, InboxGroup
from models.list import RecipientList, RecipientStatus
from models.recipient import Recipient
from models.smtp_account import SMTPAccount, EncryptionType, ProviderType
from models.user import User
from services.bulk_import_service import (
    ParseResult,
    ParsedInbox,
    ParsedList,
    ParsedSMTPAccount,
    parse_import_file,
)
from services.redis_client import get_redis_client
from services.warmup_service import WarmupService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/import", tags=["Bulk Import"])

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
IMPORT_STAGE_PREFIX = "bulk_import:stage:"
IMPORT_STAGE_TTL_SECONDS = 30 * 60


# ---------------------------------------------------------------------------
# Preview endpoint
# ---------------------------------------------------------------------------

@router.post("/preview")
async def preview_import(
    file: UploadFile = File(...),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """
    Parse the uploaded file and return a safe preview.
    No records are created. Passwords are never included in the response.
    """
    raw = await file.read()
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 10 MB.")

    filename = file.filename or "upload"
    result: ParseResult = parse_import_file(filename, raw)

    # Attach validation warnings for accounts that already exist
    existing_smtp_names = {
        a.name
        for a in db.query(SMTPAccount.name).filter(SMTPAccount.user_id == current_user.id).all()
    }
    existing_inbox_emails = {
        i.email
        for i in db.query(Inbox.email).filter(Inbox.user_id == current_user.id).all()
    }
    existing_list_names = {
        l.name
        for l in db.query(RecipientList.name).filter(RecipientList.user_id == current_user.id).all()
    }

    for acc in result.ses_accounts:
        if acc.account_name in existing_smtp_names:
            result.warnings.append(
                f"Resend account '{acc.account_name}' already exists — will be skipped on execute"
            )
    for acc in result.smtp_accounts:
        if acc.account_name in existing_smtp_names:
            result.warnings.append(
                f"SMTP account '{acc.account_name}' already exists — will be skipped on execute"
            )
    for inbox in result.inboxes:
        if inbox.email in existing_inbox_emails:
            result.warnings.append(
                f"Inbox '{inbox.email}' already exists — will be skipped on execute"
            )
    for lst in result.lists:
        if lst.list_name in existing_list_names:
            result.warnings.append(
                f"List '{lst.list_name}' already exists — recipients will be added to it on execute"
            )

    preview = result.preview()
    if result.is_valid and (
        result.ses_accounts or result.smtp_accounts or result.inboxes or result.lists
    ):
        preview["import_token"] = _stage_import_payload(result, current_user.id)

    return preview


# ---------------------------------------------------------------------------
# Execute schemas
# ---------------------------------------------------------------------------

class ExecuteResendAccount(BaseModel):
    account_name: str
    from_email_prefix: str
    from_name: str


class ExecuteSMTPAccount(BaseModel):
    account_name: str
    from_email: str
    from_name: str
    host: str
    port: int = 587
    username: str
    password: str
    encryption: str = "tls"
    hourly_limit: int = 100
    daily_limit: int = 1000
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""


class ExecuteInbox(BaseModel):
    email: str
    account_name: str
    group: str = "A"
    reply_to_email: str = ""
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    reply_enabled: Optional[bool] = None
    start_warmup: bool = True


class ExecuteRecipient(BaseModel):
    email: str
    first_name: str = ""
    last_name: str = ""


class ExecuteList(BaseModel):
    list_name: str
    list_description: str = ""
    recipients: List[ExecuteRecipient] = []


class StagedExecutePayload(BaseModel):
    ses_accounts: List[ExecuteResendAccount] = []
    smtp_accounts: List[ExecuteSMTPAccount] = []
    inboxes: List[ExecuteInbox] = []
    lists: List[ExecuteList] = []


class ExecuteRequest(BaseModel):
    import_token: str


# ---------------------------------------------------------------------------
# Execute endpoint
# ---------------------------------------------------------------------------

@router.post("/execute")
def execute_import(
    request: ExecuteRequest,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """
    Commit a bulk import. Existing records are skipped, not overwritten.
    Returns a detailed result for each entity type.
    """
    payload = _load_staged_import_payload(request.import_token, current_user.id)

    stats: Dict[str, Any] = {
        "ses_accounts": {"created": 0, "skipped": 0, "errors": [], "tested": 0, "active": 0, "failed": 0},
        "smtp_accounts": {"created": 0, "skipped": 0, "errors": []},
        "inboxes": {"created": 0, "skipped": 0, "errors": []},
        "lists": {"created": 0, "skipped": 0, "errors": []},
        "recipients": {"added": 0, "skipped": 0, "errors": []},
    }

    # Build lookup map of account names → DB id created in this run or already existing
    account_name_to_id: Dict[str, int] = {
        acc.name: acc.id
        for acc in db.query(SMTPAccount).filter(SMTPAccount.user_id == current_user.id).all()
    }

    # ── 1. Resend accounts ───────────────────────────────────────────────
    created_resend_accounts: List[tuple] = []
    for acc in payload.ses_accounts:
        if acc.account_name in account_name_to_id:
            stats["ses_accounts"]["skipped"] += 1
            continue
        try:
            verified_domain = (
                os.getenv("RESEND_VERIFIED_DOMAIN", "")
                or os.getenv("BREVO_VERIFIED_DOMAIN", "")
                or os.getenv("SES_VERIFIED_DOMAIN", "")
            )
            from_email = f"{acc.from_email_prefix}@{verified_domain}" if verified_domain else acc.from_email_prefix
            smtp = SMTPAccount(
                user_id=current_user.id,
                name=acc.account_name,
                from_email=from_email,
                from_name=acc.from_name,
                provider_type=ProviderType.BREVO,
                host="resend-api",
                port=443,
                username="resend",
                password="resend",
                encryption=EncryptionType.TLS,
            )
            db.add(smtp)
            db.flush()  # get id without committing yet
            account_name_to_id[acc.account_name] = smtp.id
            created_resend_accounts.append((smtp.id, acc.account_name))
            stats["ses_accounts"]["created"] += 1
        except Exception as exc:
            stats["ses_accounts"]["errors"].append(f"{acc.account_name}: {exc}")

    # ── 2. SMTP accounts ─────────────────────────────────────────────────
    for acc in payload.smtp_accounts:
        if acc.account_name in account_name_to_id:
            stats["smtp_accounts"]["skipped"] += 1
            continue
        try:
            enc_map = {"ssl": EncryptionType.SSL, "none": EncryptionType.NONE}
            enc = enc_map.get(acc.encryption.lower(), EncryptionType.TLS)
            smtp = SMTPAccount(
                user_id=current_user.id,
                name=acc.account_name,
                from_email=acc.from_email,
                from_name=acc.from_name,
                provider_type=ProviderType.SMTP,
                host=acc.host,
                port=acc.port,
                username=acc.username,
                password=acc.password,
                encryption=enc,
                hourly_limit=acc.hourly_limit,
                daily_limit=acc.daily_limit,
                imap_host=acc.imap_host or None,
                imap_port=acc.imap_port if acc.imap_host else None,
                imap_username=acc.imap_username or None,
                imap_password=acc.imap_password or None,
            )
            db.add(smtp)
            db.flush()
            account_name_to_id[acc.account_name] = smtp.id
            stats["smtp_accounts"]["created"] += 1
        except Exception as exc:
            stats["smtp_accounts"]["errors"].append(f"{acc.account_name}: {exc}")

    # ── 3. Inboxes ───────────────────────────────────────────────────────
    existing_emails = {
        i.email
        for i in db.query(Inbox.email).filter(Inbox.user_id == current_user.id).all()
    }

    warmup_svc = WarmupService(db)

    for inbox_data in payload.inboxes:
        if inbox_data.email in existing_emails:
            stats["inboxes"]["skipped"] += 1
            continue

        smtp_id = account_name_to_id.get(inbox_data.account_name)
        if smtp_id is None:
            stats["inboxes"]["errors"].append(
                f"{inbox_data.email}: account '{inbox_data.account_name}' not found"
            )
            continue

        try:
            group_map = {"A": InboxGroup.A, "B": InboxGroup.B, "C": InboxGroup.C}
            group = group_map.get(inbox_data.group.upper(), InboxGroup.A)

            # Resolve shared IMAP for API-backed Resend accounts from configuration.
            smtp_obj = db.query(SMTPAccount).filter(SMTPAccount.id == smtp_id).first()
            imap_host = inbox_data.imap_host
            imap_port = inbox_data.imap_port
            imap_username = inbox_data.imap_username
            imap_password = inbox_data.imap_password

            if smtp_obj and smtp_obj.provider_type in (ProviderType.SES_API, ProviderType.BREVO):
                if not imap_host:
                    imap_host = os.getenv("RESEND_IMAP_HOST", "") or os.getenv("BREVO_IMAP_HOST", "") or os.getenv("SES_IMAP_HOST", "")
                    imap_port = int(os.getenv("RESEND_IMAP_PORT", "") or os.getenv("BREVO_IMAP_PORT", "") or os.getenv("SES_IMAP_PORT", "993"))
                    imap_username = os.getenv("RESEND_IMAP_USER", "") or os.getenv("BREVO_IMAP_USER", "") or os.getenv("SES_IMAP_USER", "")
                    imap_password = os.getenv("RESEND_IMAP_PASS", "") or os.getenv("BREVO_IMAP_PASS", "") or os.getenv("SES_IMAP_PASS", "")

            has_imap = bool(imap_host and imap_username and imap_password)

            reply_enabled = inbox_data.reply_enabled
            if reply_enabled is None:
                reply_enabled = has_imap

            inbox = Inbox(
                user_id=current_user.id,
                email=inbox_data.email,
                smtp_account_id=smtp_id,
                group=group,
                reply_to_email=inbox_data.reply_to_email or None,
                imap_host=imap_host or None,
                imap_port=imap_port if imap_host else None,
                imap_username=imap_username or None,
                imap_password=imap_password or None,
                reply_enabled=reply_enabled,
            )
            db.add(inbox)
            db.flush()
            existing_emails.add(inbox_data.email)
            stats["inboxes"]["created"] += 1

            if inbox_data.start_warmup:
                try:
                    warmup_svc.start_warmup(inbox)
                except Exception as we:
                    logger.warning(f"Warmup start failed for {inbox_data.email}: {we}")

        except Exception as exc:
            stats["inboxes"]["errors"].append(f"{inbox_data.email}: {exc}")

    # ── 4. Lists + recipients ────────────────────────────────────────────
    existing_list_names = {
        lst.name: lst
        for lst in db.query(RecipientList).filter(RecipientList.user_id == current_user.id).all()
    }

    for list_data in payload.lists:
        try:
            if list_data.list_name in existing_list_names:
                recipient_list = existing_list_names[list_data.list_name]
                stats["lists"]["skipped"] += 1
            else:
                recipient_list = RecipientList(
                    user_id=current_user.id,
                    name=list_data.list_name,
                    description=list_data.list_description or None,
                    recipient_count=0,
                    active_count=0,
                )
                db.add(recipient_list)
                db.flush()
                existing_list_names[list_data.list_name] = recipient_list
                stats["lists"]["created"] += 1

            # Get emails already in this list
            existing_in_list = {
                r.email
                for r in db.query(Recipient.email).filter(
                    Recipient.list_id == recipient_list.id
                ).all()
            }

            added_count = 0
            for rec_data in list_data.recipients:
                email = rec_data.email.strip().lower()
                if email in existing_in_list:
                    stats["recipients"]["skipped"] += 1
                    continue
                if not email:
                    continue
                rec = Recipient(
                    list_id=recipient_list.id,
                    email=email,
                    first_name=rec_data.first_name or None,
                    last_name=rec_data.last_name or None,
                    status=RecipientStatus.ACTIVE,
                )
                db.add(rec)
                existing_in_list.add(email)
                added_count += 1
                stats["recipients"]["added"] += 1

            # Update list counts
            if added_count > 0:
                recipient_list.recipient_count = (recipient_list.recipient_count or 0) + added_count
                recipient_list.active_count = (recipient_list.active_count or 0) + added_count

        except Exception as exc:
            stats["lists"]["errors"].append(f"{list_data.list_name}: {exc}")

    # ── Commit everything ────────────────────────────────────────────────
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database commit failed: {exc}")

    # ── Test newly-created Resend accounts after creation is committed ───
    if created_resend_accounts:
        from api.smtp import test_smtp_account

        for smtp_id, acc_name in created_resend_accounts:
            stats["ses_accounts"]["tested"] += 1
            try:
                test_smtp_account(smtp_id, current_user=current_user, db=db)
                stats["ses_accounts"]["active"] += 1
            except HTTPException as exc:
                stats["ses_accounts"]["failed"] += 1
                logger.warning("Resend post-import test failed for %s: %s", acc_name, exc.detail)
            except Exception as exc:
                stats["ses_accounts"]["failed"] += 1
                logger.warning("Resend post-import test failed for %s: %s", acc_name, exc)

    _delete_staged_import_payload(request.import_token)

    return {
        "success": True,
        "stats": stats,
        "summary": (
            f"Created {stats['ses_accounts']['created']} Resend account(s), "
            f"{stats['smtp_accounts']['created']} SMTP account(s), "
            f"{stats['inboxes']['created']} inbox(es), "
            f"{stats['lists']['created']} list(s), "
            f"added {stats['recipients']['added']} recipient(s). "
            f"Skipped {stats['ses_accounts']['skipped'] + stats['smtp_accounts']['skipped']} account(s), "
            f"{stats['inboxes']['skipped']} inbox(es), "
            f"{stats['recipients']['skipped']} duplicate recipient(s)."
            + (
                f" Tested {stats['ses_accounts']['tested']} Resend account(s): "
                f"{stats['ses_accounts']['active']} active, {stats['ses_accounts']['failed']} failed."
                if stats["ses_accounts"]["tested"] > 0
                else ""
            )
        ),
    }

def _get_import_stage_store():
    return get_redis_client()


def _serialize_import_result(result: ParseResult) -> Dict[str, Any]:
    """Build the private execute payload that must never be returned to the browser."""
    return {
        "ses_accounts": [
            {
                "account_name": account.account_name,
                "from_email_prefix": account.from_email_prefix,
                "from_name": account.from_name,
                "aws_region": account.aws_region,
            }
            for account in result.ses_accounts
        ],
        "smtp_accounts": [
            {
                "account_name": account.account_name,
                "from_email": account.from_email,
                "from_name": account.from_name,
                "host": account.host,
                "port": account.port,
                "username": account.username,
                "password": account.password,
                "encryption": account.encryption,
                "hourly_limit": account.hourly_limit,
                "daily_limit": account.daily_limit,
                "imap_host": account.imap_host,
                "imap_port": account.imap_port,
                "imap_username": account.imap_username,
                "imap_password": account.imap_password,
            }
            for account in result.smtp_accounts
        ],
        "inboxes": [
            {
                "email": inbox.email,
                "account_name": inbox.account_name,
                "group": inbox.group,
                "reply_to_email": inbox.reply_to_email,
                "imap_host": inbox.imap_host,
                "imap_port": inbox.imap_port,
                "imap_username": inbox.imap_username,
                "imap_password": inbox.imap_password,
                "reply_enabled": inbox.reply_enabled,
                "start_warmup": inbox.start_warmup,
            }
            for inbox in result.inboxes
        ],
        "lists": [
            {
                "list_name": recipient_list.list_name,
                "list_description": recipient_list.list_description,
                "recipients": [
                    {
                        "email": recipient.email,
                        "first_name": recipient.first_name,
                        "last_name": recipient.last_name,
                    }
                    for recipient in recipient_list.recipients
                ],
            }
            for recipient_list in result.lists
        ],
    }


def _stage_import_payload(result: ParseResult, user_id: int) -> str:
    """Store a parsed import temporarily so secrets never return to the browser."""
    import_token = secrets.token_urlsafe(32)
    staged_payload = {
        "user_id": user_id,
        "payload": _serialize_import_result(result),
    }

    try:
        _get_import_stage_store().setex(
            f"{IMPORT_STAGE_PREFIX}{import_token}",
            IMPORT_STAGE_TTL_SECONDS,
            json.dumps(staged_payload),
        )
    except RedisError as exc:
        logger.error("Unable to stage bulk import for user %s: %s", user_id, exc)
        raise HTTPException(
            status_code=503,
            detail="Unable to prepare this import. Please try uploading the file again.",
        )

    return import_token


def _load_staged_import_payload(import_token: str, user_id: int) -> StagedExecutePayload:
    """Retrieve and validate the staged import for the authenticated owner only."""
    try:
        raw_payload = _get_import_stage_store().get(f"{IMPORT_STAGE_PREFIX}{import_token}")
    except RedisError as exc:
        logger.error("Unable to retrieve staged bulk import for user %s: %s", user_id, exc)
        raise HTTPException(
            status_code=503,
            detail="Unable to retrieve this import. Please try again shortly.",
        )

    if not raw_payload:
        raise HTTPException(
            status_code=410,
            detail="This import preview has expired. Upload the file again to create a new preview.",
        )

    try:
        staged_payload = json.loads(raw_payload)
        if staged_payload.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="This import preview belongs to another user.")
        return StagedExecutePayload.model_validate(staged_payload["payload"])
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        logger.error("Invalid staged bulk import payload for user %s: %s", user_id, exc)
        _delete_staged_import_payload(import_token)
        raise HTTPException(
            status_code=500,
            detail="This import preview is no longer valid. Upload the file again.",
        )


def _delete_staged_import_payload(import_token: str) -> None:
    try:
        _get_import_stage_store().delete(f"{IMPORT_STAGE_PREFIX}{import_token}")
    except RedisError as exc:
        logger.warning("Unable to clear staged bulk import %s: %s", import_token[:8], exc)
