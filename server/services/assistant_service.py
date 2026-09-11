"""Persistence and guarded action handling for Fleet Assistant."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.assistant import AssistantConversation, AssistantMessage, AssistantPendingAction
from models.campaign import Campaign
from models.inbox import Inbox
from models.list import RecipientList
from models.ses_template import SESEmailTemplate
from models.smtp_account import SMTPAccount
from models.user import User


DELETE_RESOURCE_CONFIG = {
    "campaign": {
        "model": Campaign,
        "label_column": Campaign.name,
        "aliases": ("campaigns", "campaign"),
        "warning": "This permanently deletes the campaign, its send records, queued recipient tracking, replies, and related email records.",
    },
    "inbox": {
        "model": Inbox,
        "label_column": Inbox.email,
        "aliases": ("inboxes", "inbox"),
        "warning": "This permanently deletes the inbox, warmup data, monitoring data, and related email records. Campaign tracking is unlinked from the inbox.",
    },
    "smtp_account": {
        "model": SMTPAccount,
        "label_column": SMTPAccount.name,
        "aliases": ("smtp accounts", "smtp account", "sending accounts", "sending account"),
        "warning": "This permanently deletes the sending account and every inbox linked to it, including their warmup and monitoring data.",
    },
    "list": {
        "model": RecipientList,
        "label_column": RecipientList.name,
        "aliases": ("recipient lists", "recipient list", "lists", "list"),
        "warning": "This permanently deletes the list and its recipients. Campaigns using the list are detached from it.",
    },
    "template": {
        "model": SESEmailTemplate,
        "label_column": SESEmailTemplate.name,
        "aliases": ("email templates", "email template", "templates", "template"),
        "warning": "This permanently deletes the email template.",
    },
}

CONFIRM_WORDS = {
    "yes",
    "yes delete it",
    "confirm",
    "confirm delete",
    "delete it",
    "do it",
    "proceed",
}
CANCEL_WORDS = {"no", "cancel", "cancel it", "never mind", "nevermind", "stop"}

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_conversation(db: Session, user_id: int, title: str = "New conversation") -> AssistantConversation:
    conversation = AssistantConversation(user_id=user_id, title=title[:255], updated_at=utcnow())
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def get_conversation(db: Session, user_id: int, conversation_id: int) -> AssistantConversation:
    conversation = db.query(AssistantConversation).filter(
        AssistantConversation.id == conversation_id,
        AssistantConversation.user_id == user_id,
        AssistantConversation.is_archived.is_(False),
    ).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


def list_conversations(db: Session, user_id: int, limit: int = 50) -> List[AssistantConversation]:
    return db.query(AssistantConversation).filter(
        AssistantConversation.user_id == user_id,
        AssistantConversation.is_archived.is_(False),
    ).order_by(AssistantConversation.updated_at.desc()).limit(min(max(limit, 1), 100)).all()


def archive_conversation(db: Session, user_id: int, conversation_id: int) -> None:
    conversation = get_conversation(db, user_id, conversation_id)
    conversation.is_archived = True
    conversation.updated_at = utcnow()
    db.commit()


def add_message(
    db: Session,
    conversation: AssistantConversation,
    role: str,
    content: str,
    action: Optional[Dict[str, Any]] = None,
) -> AssistantMessage:
    message = AssistantMessage(
        conversation_id=conversation.id,
        role=role,
        content=content,
        action_json=json.dumps(action) if action else None,
    )
    conversation.updated_at = utcnow()
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def _aggregate_status(statuses: List[str]) -> str:
    if any(status == "pending" for status in statuses):
        return "pending"
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "completed" for status in statuses):
        return "completed"
    return statuses[0] if statuses else "cancelled"


def _format_label_list(labels: List[str]) -> str:
    bolded = [f"**{label}**" for label in labels]
    if len(bolded) == 1:
        return bolded[0]
    if len(bolded) == 2:
        return f"{bolded[0]} and {bolded[1]}"
    return ", ".join(bolded[:-1]) + f", and {bolded[-1]}"


def serialize_action(db: Session, action: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not action:
        return None
    serialized = dict(action)
    action_ids = serialized.get("action_ids")
    if action_ids:
        rows = db.query(AssistantPendingAction).filter(AssistantPendingAction.id.in_(action_ids)).all()
        if rows:
            serialized["status"] = _aggregate_status([row.status for row in rows])
    return serialized


def serialize_message(db: Session, message: AssistantMessage) -> Dict[str, Any]:
    action = None
    if message.action_json:
        try:
            action = json.loads(message.action_json)
        except (TypeError, ValueError):
            action = None
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "action": serialize_action(db, action),
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def serialize_conversation_summary(conversation: AssistantConversation) -> Dict[str, Any]:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
    }


def title_from_message(message: str) -> str:
    compact = " ".join(message.split())
    return compact[:60] + ("…" if len(compact) > 60 else "") or "New conversation"


def get_recent_messages(db: Session, conversation_id: int, limit: int = 12) -> List[AssistantMessage]:
    messages = db.query(AssistantMessage).filter(
        AssistantMessage.conversation_id == conversation_id,
    ).order_by(AssistantMessage.created_at.desc(), AssistantMessage.id.desc()).limit(limit).all()
    return list(reversed(messages))


def _resource_label(resource_type: str, resource: Any) -> str:
    if resource_type == "inbox":
        return resource.email
    return resource.name


def _resource_detail(resource_type: str, resource: Any) -> str:
    if resource_type == "campaign":
        status = getattr(resource.status, "value", resource.status)
        return f"Campaign · {status}"
    if resource_type == "inbox":
        state = getattr(resource.state, "value", resource.state)
        return f"Inbox · {state}"
    if resource_type == "smtp_account":
        provider = getattr(resource.provider_type, "value", resource.provider_type)
        return f"Sending account · {provider or 'smtp'}"
    if resource_type == "list":
        return f"Recipient list · {resource.recipient_count or 0} recipients"
    if resource_type == "template":
        return "Email template"
    return resource_type.replace("_", " ").title()


def _owned_resource_query(db: Session, user_id: int, resource_type: str):
    config = DELETE_RESOURCE_CONFIG[resource_type]
    model = config["model"]
    return db.query(model).filter(model.user_id == user_id)


def get_owned_resource(db: Session, user_id: int, resource_type: str, resource_id: int):
    if resource_type not in DELETE_RESOURCE_CONFIG:
        raise HTTPException(status_code=400, detail="Unsupported resource type")
    resource = _owned_resource_query(db, user_id, resource_type).filter(
        DELETE_RESOURCE_CONFIG[resource_type]["model"].id == resource_id
    ).first()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found or not owned by this account")
    return resource


def list_resource_candidates(
    db: Session,
    user_id: int,
    resource_type: str,
    selector: str = "",
    limit: int = 12,
) -> List[Dict[str, Any]]:
    config = DELETE_RESOURCE_CONFIG[resource_type]
    model = config["model"]
    query = _owned_resource_query(db, user_id, resource_type)
    selector = selector.strip()

    numeric_match = re.fullmatch(r"#?(\d+)", selector)
    if numeric_match:
        query = query.filter(model.id == int(numeric_match.group(1)))
    elif selector:
        normalized = selector.casefold()
        exact = query.filter(func.lower(config["label_column"]) == normalized).all()
        resources = exact or query.filter(config["label_column"].ilike(f"%{selector}%")).limit(limit).all()
        return [
            {
                "id": resource.id,
                "label": _resource_label(resource_type, resource),
                "detail": _resource_detail(resource_type, resource),
            }
            for resource in resources
        ]

    resources = query.order_by(model.id.desc()).limit(limit).all()
    return [
        {
            "id": resource.id,
            "label": _resource_label(resource_type, resource),
            "detail": _resource_detail(resource_type, resource),
        }
        for resource in resources
    ]


def resolve_single_resource(
    db: Session,
    user_id: int,
    resource_type: str,
    selector: str,
) -> Tuple[Optional[int], Optional[str], Optional[Tuple[str, Optional[Dict[str, Any]]]]]:
    """Resolve a free-text selector to exactly one owned resource.

    Returns (resource_id, label, None) on exactly one match, or
    (None, None, (reply, action)) when the caller should return that
    reply/action to the user immediately instead (zero or multiple matches).
    """
    candidates = list_resource_candidates(db, user_id, resource_type, selector)
    friendly_type = resource_type.replace("_", " ")
    if not candidates:
        return None, None, (f"I couldn't find an owned {friendly_type} matching **{selector or 'that request'}**.", None)
    if len(candidates) == 1:
        return candidates[0]["id"], candidates[0]["label"], None

    reply = (
        f"I found {len(candidates)} matching {friendly_type}s. Which one did you mean?"
        if selector
        else f"Which {friendly_type} did you mean?"
    )
    return None, None, (reply, {
        "type": "action_selection",
        "resource_type": resource_type,
        "candidates": candidates,
    })


def _extract_delete_request(message: str) -> Optional[Tuple[Optional[str], str]]:
    compact = " ".join(message.strip().split())
    lowered = compact.casefold()
    delete_match = re.search(r"\b(delete|remove)\b", lowered)
    if not delete_match:
        return None
    if re.search(r"\b(how|can i|could i|where)\b.*\b(delete|remove)\b", lowered):
        return None

    after_delete = lowered[delete_match.end():]
    best_match = None
    for resource_type, config in DELETE_RESOURCE_CONFIG.items():
        for alias in config["aliases"]:
            match = re.search(rf"\b{re.escape(alias)}\b", after_delete)
            if match and (best_match is None or match.start() < best_match[0]):
                best_match = (match.start(), match.end(), resource_type)

    if not best_match:
        return None, ""

    _, alias_end, resource_type = best_match
    selector_start = delete_match.end() + alias_end
    selector = compact[selector_start:].strip(" :#\"'")
    selector = re.sub(r"^(named|called|with id|id)\s+", "", selector, flags=re.IGNORECASE)
    return resource_type, selector.strip(" :#\"'")


def _extract_delete_follow_up(
    db: Session,
    conversation_id: int,
    message: str,
) -> Optional[Tuple[Optional[str], str]]:
    previous = db.query(AssistantMessage).filter(
        AssistantMessage.conversation_id == conversation_id,
        AssistantMessage.role == "assistant",
    ).order_by(AssistantMessage.created_at.desc(), AssistantMessage.id.desc()).first()
    if not previous:
        return None

    if previous.action_json:
        try:
            previous_action = json.loads(previous.action_json)
        except (TypeError, ValueError):
            previous_action = None
        if previous_action and previous_action.get("type") == "delete_selection":
            return previous_action.get("resource_type"), message.strip()

    if not previous.content.startswith("What would you like to delete?"):
        return None

    compact = " ".join(message.strip().split())
    lowered = compact.casefold()
    for resource_type, config in DELETE_RESOURCE_CONFIG.items():
        for alias in config["aliases"]:
            match = re.match(rf"^(?:my\s+)?{re.escape(alias)}\b", lowered)
            if match:
                selector = compact[match.end():].strip(" :#\"'")
                return resource_type, selector
    return None


def list_pending_actions(db: Session, user_id: int, conversation_id: int) -> List[AssistantPendingAction]:
    return db.query(AssistantPendingAction).filter(
        AssistantPendingAction.user_id == user_id,
        AssistantPendingAction.conversation_id == conversation_id,
        AssistantPendingAction.status == "pending",
    ).order_by(AssistantPendingAction.created_at.asc(), AssistantPendingAction.id.asc()).all()


def prepare_delete_action(
    db: Session,
    user_id: int,
    conversation: AssistantConversation,
    resource_type: str,
    resource_ids: List[int],
) -> Tuple[str, Dict[str, Any]]:
    resources = [get_owned_resource(db, user_id, resource_type, resource_id) for resource_id in resource_ids]
    labels = [_resource_label(resource_type, resource) for resource in resources]
    config = DELETE_RESOURCE_CONFIG[resource_type]

    for existing in db.query(AssistantPendingAction).filter(
        AssistantPendingAction.user_id == user_id,
        AssistantPendingAction.conversation_id == conversation.id,
        AssistantPendingAction.status == "pending",
    ).all():
        existing.status = "cancelled"
        existing.resolved_at = utcnow()

    pending_rows = []
    for resource, label in zip(resources, labels):
        pending = AssistantPendingAction(
            conversation_id=conversation.id,
            user_id=user_id,
            action_type="delete",
            resource_type=resource_type,
            resource_id=resource.id,
            resource_label=label,
            status="pending",
        )
        db.add(pending)
        pending_rows.append(pending)

    db.commit()
    for pending in pending_rows:
        db.refresh(pending)

    reply = (
        f"Please confirm permanent deletion of {_format_label_list(labels)}.\n\n"
        f"{config['warning']}\n\n"
        "Choose **Confirm delete** below, or say “yes, delete it”."
    )
    action = {
        "type": "delete_confirmation",
        "action_ids": [pending.id for pending in pending_rows],
        "resource_type": resource_type,
        "resource_ids": [pending.resource_id for pending in pending_rows],
        "labels": labels,
        "warning": config["warning"],
        "status": "pending",
    }
    return reply, action


def handle_delete_intent(
    db: Session,
    user: User,
    conversation: AssistantConversation,
    message: str,
) -> Optional[Tuple[str, Optional[Dict[str, Any]]]]:
    normalized = " ".join(message.casefold().strip().split())
    pending_actions = list_pending_actions(db, user.id, conversation.id)
    if pending_actions and normalized in CONFIRM_WORDS:
        return confirm_delete_actions(db, user, [pending.id for pending in pending_actions])
    if pending_actions and normalized in CANCEL_WORDS:
        return cancel_delete_actions(db, user, [pending.id for pending in pending_actions])

    parsed = _extract_delete_request(message)
    if parsed is None:
        parsed = _extract_delete_follow_up(db, conversation.id, message)
    if parsed is None:
        return None

    resource_type, selector = parsed
    if resource_type is None:
        return (
            "What would you like to delete? I can safely delete one of your **campaigns, inboxes, sending accounts, recipient lists, or templates**. Tell me the type and name.",
            None,
        )

    candidates = list_resource_candidates(db, user.id, resource_type, selector)
    friendly_type = resource_type.replace("_", " ")
    if not candidates:
        return (f"I couldn't find an owned {friendly_type} matching **{selector or 'that request'}**.", None)
    if len(candidates) == 1:
        return prepare_delete_action(db, user.id, conversation, resource_type, [candidates[0]["id"]])

    reply = (
        f"I found {len(candidates)} matching {friendly_type}s. Which one do you want to delete?"
        if selector
        else f"Which {friendly_type} do you want to delete?"
    )
    return reply, {
        "type": "delete_selection",
        "resource_type": resource_type,
        "candidates": candidates,
    }


def _execute_owned_delete(db: Session, user: User, action: AssistantPendingAction) -> None:
    # Re-check strict ownership immediately before the destructive operation.
    get_owned_resource(db, user.id, action.resource_type, action.resource_id)

    if action.resource_type == "campaign":
        from api.campaign import delete_campaign
        delete_campaign(action.resource_id, force="true", current_user=user, db=db)
    elif action.resource_type == "inbox":
        from api.inbox import delete_inbox
        delete_inbox(action.resource_id, force=True, current_user=user, db=db)
    elif action.resource_type == "smtp_account":
        from api.smtp import delete_smtp_account
        delete_smtp_account(action.resource_id, force=True, current_user=user, db=db)
    elif action.resource_type == "list":
        from api.list import delete_list
        delete_list(
            action.resource_id,
            delete_recipients=True,
            force=True,
            current_user=user,
            db=db,
        )
    elif action.resource_type == "template":
        from api.fancy_email_template import delete_template
        delete_template(action.resource_id, current_user=user, db=db)
    else:
        raise HTTPException(status_code=400, detail="Unsupported delete action")


def confirm_delete_actions(
    db: Session,
    user: User,
    action_ids: List[int],
) -> Tuple[str, Dict[str, Any]]:
    pending_rows = db.query(AssistantPendingAction).filter(
        AssistantPendingAction.id.in_(action_ids),
        AssistantPendingAction.user_id == user.id,
    ).all()
    if not pending_rows:
        raise HTTPException(status_code=404, detail="Delete confirmation not found")

    deleted_labels: List[str] = []
    failed: List[Tuple[str, str]] = []
    already_resolved: List[Tuple[str, str]] = []

    for pending in pending_rows:
        if pending.status != "pending":
            already_resolved.append((pending.resource_label, pending.status))
            continue
        try:
            _execute_owned_delete(db, user, pending)
            pending.status = "completed"
            pending.resolved_at = utcnow()
            db.commit()
            deleted_labels.append(pending.resource_label)
        except HTTPException as exc:
            db.rollback()
            pending.status = "failed"
            pending.resolved_at = utcnow()
            db.commit()
            failed.append((pending.resource_label, str(exc.detail)))
        except IntegrityError:
            db.rollback()
            pending.status = "failed"
            pending.resolved_at = utcnow()
            db.commit()
            logger.exception("Delete failed (integrity error): %s #%s", pending.resource_type, pending.resource_id)
            failed.append((pending.resource_label, "it's still linked to other records"))
        except Exception:
            db.rollback()
            pending.status = "failed"
            pending.resolved_at = utcnow()
            db.commit()
            logger.exception("Delete failed: %s #%s", pending.resource_type, pending.resource_id)
            failed.append((pending.resource_label, "an unexpected error occurred"))

    reply_parts = []
    if deleted_labels:
        reply_parts.append(f"Deleted {_format_label_list(deleted_labels)} successfully.")
    if failed:
        failed_bits = "; ".join(f"**{label}**: {error}" for label, error in failed)
        reply_parts.append(f"Failed to delete {failed_bits}.")
    if already_resolved:
        resolved_bits = "; ".join(f"**{label}** (already {status})" for label, status in already_resolved)
        reply_parts.append(f"Skipped {resolved_bits}.")
    reply = " ".join(reply_parts) if reply_parts else "Nothing to confirm."

    return reply, {
        "type": "delete_confirmation",
        "action_ids": [pending.id for pending in pending_rows],
        "resource_type": pending_rows[0].resource_type,
        "resource_ids": [pending.resource_id for pending in pending_rows],
        "labels": [pending.resource_label for pending in pending_rows],
        "status": _aggregate_status([pending.status for pending in pending_rows]),
    }


def cancel_delete_actions(
    db: Session,
    user: User,
    action_ids: List[int],
) -> Tuple[str, Dict[str, Any]]:
    pending_rows = db.query(AssistantPendingAction).filter(
        AssistantPendingAction.id.in_(action_ids),
        AssistantPendingAction.user_id == user.id,
    ).all()
    if not pending_rows:
        raise HTTPException(status_code=404, detail="Delete confirmation not found")

    cancelled_labels: List[str] = []
    for pending in pending_rows:
        if pending.status == "pending":
            pending.status = "cancelled"
            pending.resolved_at = utcnow()
            cancelled_labels.append(pending.resource_label)
    db.commit()

    if cancelled_labels:
        reply = f"Deletion of {_format_label_list(cancelled_labels)} was cancelled."
    else:
        reply = "Nothing to cancel."

    return reply, {
        "type": "delete_confirmation",
        "action_ids": [pending.id for pending in pending_rows],
        "resource_type": pending_rows[0].resource_type,
        "resource_ids": [pending.resource_id for pending in pending_rows],
        "labels": [pending.resource_label for pending in pending_rows],
        "status": _aggregate_status([pending.status for pending in pending_rows]),
    }
