"""
Tool registry for Fleet Assistant function-calling.

A fixed, hand-written list of callable actions the assistant may invoke via
Gemini function calling. Every tool resolves a free-text `selector` against
the current user's owned resources (via assistant_service.resolve_single_resource)
before running — the model never supplies a raw database id, and every tool
here is read-only or a safe/reversible mutation (no confirmation required).
Destructive/costly actions live behind the guarded-confirmation flow instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models.user import User

TOOL_DECLARATIONS = [
    {
        "name": "test_smtp_account",
        "description": (
            "Test the connection/API status of one of the user's sending accounts "
            "(SMTP or Resend) by name, and report whether it's active."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "selector": {"type": "STRING", "description": "The sending account's name, or #id."},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "get_campaign_status",
        "description": "Get the current status and send progress of one of the user's campaigns by name.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "selector": {"type": "STRING", "description": "The campaign's name, or #id."},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "get_warmup_status",
        "description": "Get the current warmup progress and state of one of the user's inboxes by email address.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "selector": {"type": "STRING", "description": "The inbox's email address, or #id."},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "prepare_campaigns",
        "description": (
            "Generate campaigns from the bundle that was imported in this conversation — "
            "one campaign per recipient list, reusing the imported templates. "
            "Call this whenever the user asks to create campaigns."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        },
    },
]


def _run_test_smtp_account(db: Session, user: User, resource_id: int) -> str:
    from api.smtp import test_smtp_account

    try:
        result = test_smtp_account(resource_id, current_user=user, db=db)
        return f"Test succeeded: {result.get('message', 'connection OK')} (status: {result.get('status', 'active')})."
    except HTTPException as exc:
        return f"Test failed: {exc.detail}"
    except Exception as exc:
        return f"Test failed: {exc}"


def _run_get_campaign_status(db: Session, user: User, resource_id: int) -> str:
    from services.campaign_service import CampaignService

    stats = CampaignService(db).get_campaign_stats(resource_id)
    if not stats:
        return "Campaign not found."
    return (
        f"Campaign '{stats['name']}' is {stats['status']}. "
        f"Sent {stats['total_sent']} of {stats['total_recipients']} recipients, "
        f"{stats['total_failed']} failed, {stats['total_replies']} replies, "
        f"{stats['opens']} opens, {stats['clicks']} clicks."
    )


def _run_get_warmup_status(db: Session, user: User, resource_id: int) -> str:
    from models.inbox import Inbox
    from services.warmup_service import WarmupService

    inbox = db.query(Inbox).filter(Inbox.id == resource_id).first()
    if not inbox:
        return "Inbox not found."
    status = WarmupService(db).get_warmup_status(inbox)
    parts = [f"Inbox {status['email']} is in state '{status['state']}'"]
    if "days_elapsed" in status:
        parts.append(f"day {inbox.warmup_day} of warmup, {status.get('progress_percent', 0)}% through the ramp")
    parts.append(f"daily cap {status['daily_cap']}, sent {status['current_daily_count']} today")
    if status.get("pause_reason"):
        parts.append(f"paused: {status['pause_reason']}")
    return "; ".join(parts) + "."


@dataclass
class ToolSpec:
    resource_type: str
    requires_confirmation: bool
    run: Callable[[Session, User, int], str]


TOOL_REGISTRY: Dict[str, ToolSpec] = {
    "test_smtp_account": ToolSpec("smtp_account", False, _run_test_smtp_account),
    "get_campaign_status": ToolSpec("campaign", False, _run_get_campaign_status),
    "get_warmup_status": ToolSpec("inbox", False, _run_get_warmup_status),
}
