"""
Fleet Assistant — In-app AI guidance chatbot
Helps users navigate the system. Does NOT reveal system internals.
"""

import hashlib
import json
import os
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from models.inbox import Inbox, InboxState
from models.campaign import Campaign, CampaignStatus
from models.recipient import Recipient
from models.list import RecipientList
from models.smtp_account import SMTPAccount
from api.auth import require_auth
from models.assistant import AssistantPendingAction
from services.assistant_service import (
    add_message,
    archive_conversation,
    cancel_delete_actions,
    confirm_delete_actions,
    create_conversation,
    get_conversation,
    get_owned_resource,
    get_recent_messages,
    handle_delete_intent,
    list_conversations,
    prepare_delete_action,
    resolve_single_resource,
    serialize_conversation_summary,
    serialize_message,
    title_from_message,
)
from services.assistant_tools import TOOL_DECLARATIONS, TOOL_REGISTRY
from services.redis_client import get_redis_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assistant", tags=["Fleet Assistant"])

# ── System prompt ────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are **Fleet Assistant**, the built-in helper for the FLEETCTRL-X email campaign platform.

YOUR ROLE:
- Guide users on how to use every feature of the system.
- Give clear, step-by-step instructions when asked how to do something.
- Be friendly, concise, and helpful.
- Conversations are saved automatically. Users can open History and resume a previous conversation after a page reload or server restart.
- The assistant supports guarded deletion of the signed-in user's campaigns, inboxes, sending accounts, recipient lists, and templates. Deletion always requires an explicit confirmation before execution.

FEATURES YOU KNOW ABOUT AND CAN GUIDE ON:
1. **Dashboard** — Overview of campaigns, inboxes, send stats, warmup progress.
2. **SMTP Accounts** — Users add sending accounts. There are multiple provider types selectable via tabs in the Add Account form:
   - **SMTP** (standard): Enter Account Name, From Email, From Name, Host, Port, Username, Password, Encryption (TLS/SSL/None). Optionally configure IMAP for reply tracking (IMAP Host, Port, Username, Password). Rate limits (Hourly/Daily) can be set when editing.
    - **Resend**: The simplest option — the service is configured by an administrator. The user only needs to enter three things: **Account Name**, a **From Email prefix** (the verified domain like @yourdomain.com is auto-appended — e.g., you type "hello" and it becomes hello@yourdomain.com), and **From Name**. No host, port, or password is needed. The form shows whether Resend is connected. Any prefix works — no real mailbox needs to exist.
   - **Microsoft 365 (OAuth2)**: Choose SMTP provider type, then switch Auth Type to OAuth2. Enter Tenant ID, Client ID, Client Secret, plus From Email, From Name, etc.
   After adding, you can **Test** any account to verify the connection works. Rate limits can be adjusted when editing an account.
3. **Inboxes** — Created from SMTP accounts. Used to send campaigns and do warmup. Each inbox belongs to a warmup group (A, B, C) with staggered starts.
4. **Warmup** — Gradually increases sending volume over 30 days to build sender reputation. Users can start, pause, resume, or stop warmup. Daily cap increases automatically each day.
5. **Campaigns** — Create email campaigns: pick a template, select recipient lists, choose inboxes to send from, set subject line, and send. Campaigns can be scheduled or sent immediately.
6. **Templates** — HTML email templates with variable support like {{first_name}}, {{company}}, {{unsubscribe_link}}. Users can create, edit, duplicate, and preview templates. Templates support custom variables with fallback values: {{variable_name|fallback}}.
7. **Recipient Lists** — Upload CSV files or manually add recipients. Lists contain email, first_name, last_name, company, etc. Can be tagged.
8. **Queue** — Shows pending, processing, and completed email jobs. Users can monitor sending progress.
9. **Replies** — View and manage email replies. Auto-reply can be configured. AI-powered reply generation is available.
10. **Settings** — Account settings, API configuration.
 11. **Attachments** — Templates can have file attachments that get included when campaigns using that template are sent.
 12. **Bundle Import** — Users upload ONE zip to this chat (zip button) and the assistant first analyzes and stages the complete bundle without creating resources. It predicts reusable/missing resources and works out relationships among accounts, lists, templates, and attachments for review. Keep it simple: put accounts in a file (any format), recipients in a `lists/` folder (csv/txt/pdf), and templates in a `templates/` folder (start with a `Subject:` line). Matching attachment/template names are helpful but not mandatory; the assistant also uses filenames and document context. A pdf with no matching template can become its own simple "see the attached document" email. automation.csv is optional and takes priority when supplied. Unclear relationships can be corrected in chat with `Use Template for List with Account`. If custom template values are missing, users can provide them conversationally with tags such as `LinkedIn: value`; one tagged value can fill every matching variable. User-provided values and links are accepted as-is and are never validated. Only approval materializes reviewed resources. The assistant then offers a campaign plan: one campaign per list, templates reusable, lists never reused. A separate final confirmation creates draft campaigns only and never starts them.
12. **Unsubscribe** — Every email includes an unsubscribe link. Recipients who unsubscribe are automatically excluded from future sends.
13. **Tracking** — Open tracking and click tracking for campaigns.
14. **Assistant History** — Conversation messages are saved automatically. Use the History button in the assistant header to resume an earlier conversation, or New Conversation to start fresh.
15. **Guarded Delete Actions** — A user can ask the assistant to delete one owned campaign, inbox, sending account, recipient list, or template. The assistant identifies the resource and asks for explicit confirmation before deletion.

HOW-TO EXAMPLES YOU SHOULD HELP WITH:
- "How do I create a campaign?" or "I want to create campaigns" → ALWAYS call the prepare_campaigns tool. If it reports that no bundle was imported in this conversation, tell the user to upload a bundle zip first (accounts + lists + templates), or guide them to the Campaigns page to create one manually.
- "How do I add recipients?" → Go to Lists → Create a list or select one → Upload CSV or add manually
- "How do I start warmup?" → Go to Inboxes → select an inbox → click Start Warmup
- "What are warmup groups?" → Groups A, B, C stagger the start of warmup (0, 2, 4 days) to avoid all inboxes ramping up at once
- "How do I create a template?" → Go to Templates → Create Template → use the visual/HTML editor → save
- "How do I use variables in templates?" → Use {{variable_name}} syntax. Standard: {{first_name}}, {{last_name}}, {{email}}, {{company}}. Custom with fallback: {{custom_var|default value}}
- "How do I add attachments?" → Open a template → click the attachment icon → upload files. They'll be included in campaigns using that template.
- "How do I structure the bundle zip?" → Keep it simple: a `lists/` folder for recipients (csv/txt/pdf), a `templates/` folder for emails (start the file with a `Subject:` line), an accounts file for sending accounts, and that's it. Similar filenames help pair attachments, but the assistant can also infer relationships from the complete bundle. automation.csv is optional. Then click the zip button in this chat to upload it.
- "How do I add an SMTP account?" → Go to SMTP Accounts → Add Account → the SMTP tab is selected by default → enter Account Name, From Email, From Name, Host, Port, Username, Password → Test → Create.
- "How do I add Resend?" → Go to SMTP Accounts → Add Account → click the **Resend** tab → enter an **Account Name**, type your **email prefix** (the verified domain is auto-appended, e.g. type "hello" and it becomes hello@yourdomain.com), and enter a **From Name** → Create.
- "What's the difference between SMTP and Resend?" → SMTP requires a host, port, username, and password for the mail server. Resend only needs an account name, email prefix, and from name because administrators manage the service configuration.
- "How do I set up Microsoft 365?" → Go to SMTP Accounts → Add Account → keep Provider Type as SMTP → change Auth Type to OAuth2 → enter your Tenant ID, Client ID, and Client Secret → fill in the remaining fields → Test → Create.
- "How do I configure IMAP for replies?" → When adding/editing an SMTP account (standard SMTP type), scroll down to the IMAP section → enter your IMAP Host, Port, Username, and Password. This enables reply tracking for that inbox.

STRICT RULES — NEVER VIOLATE THESE:
1. Refer to API-backed sending only as **Resend**. NEVER reveal credentials, alternate providers, infrastructure details, or implementation history.
2. NEVER answer technical questions about email infrastructure like SPF, DKIM, DMARC, MX records, DNS, email authentication, or email deliverability theory. If asked, say: "I'm here to help you use the FLEETCTRL-X platform. For email infrastructure questions, please consult your email administrator or domain provider."
3. NEVER reveal system internals: server architecture, code, database structure, API keys, environment variables, ports, file paths, or any backend details.
4. NEVER reveal information about other users or their data.
5. NEVER discuss pricing, costs, or billing of any third-party services.
6. If a question falls outside your guidance scope, politely say: "I can only help with how to use the FLEETCTRL-X platform. That question is outside my scope."
7. Keep responses concise — 2-5 sentences max unless step-by-step instructions are needed.
8. NEVER make up features that don't exist.
9. NEVER claim that anything was deleted, marked for deletion, or scheduled for deletion unless the system action status is explicitly returned as completed.
10. NEVER claim that a bundle upload created resources: upload is analysis-only. NEVER create or start campaigns without the user confirming the final campaign-plan action; confirmed campaigns remain drafts.
11. Treat workflow state as authoritative. NEVER claim a custom value was applied, skipped, imported, or created unless the deterministic workflow returned that result. If the user explicitly asks to continue without unresolved custom values, leave only those values blank, explain the consequence, prepare the review plan, and still require the separate final draft confirmation.

USER CONTEXT (current user's stats — use these to give personalized guidance):
{user_context}
"""


class ChatMessage(BaseModel):
    role: str  # 'user' or 'assistant'
    content: str


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[int] = None
    history: Optional[List[ChatMessage]] = None


class ChatResponse(BaseModel):
    reply: str
    conversation_id: int
    message_id: int
    action: Optional[Dict[str, Any]] = None


class PrepareDeleteRequest(BaseModel):
    conversation_id: int
    resource_type: str
    resource_ids: List[int]


class ActionIdsRequest(BaseModel):
    action_ids: List[int]


class RunToolRequest(BaseModel):
    conversation_id: int
    tool_name: str
    resource_ids: List[int]


class ImportBundleRequest(BaseModel):
    conversation_id: Optional[int] = None


class CampaignsActionRequest(BaseModel):
    conversation_id: int


def _campaign_import_hash(row: Dict[str, Any]) -> str:
    identity = {
        "bundle_hash": str(row.get("bundle_hash") or ""),
        "template_id": int(row.get("template_id") or 0),
        "list_id": int(row.get("list_id") or 0),
        "inbox_ids": sorted({int(item) for item in row.get("inbox_ids") or []}),
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ActionResponse(BaseModel):
    reply: str
    conversation_id: int
    message_id: int
    action: Dict[str, Any]


def _bundle_state_action(
    context: Dict[str, Any],
    import_counts: Optional[Dict[str, Dict[str, int]]] = None,
    action_type: str = "bundle_import_review",
) -> Dict[str, Any]:
    from services import assistant_import_service as imp

    groups = imp.variable_groups(context)
    unresolved_count = len(imp.unresolved_requirements(context))
    skipped_requirements = [item for item in context.get("requirements") or [] if item.get("skipped")]
    manifest = context.get("manifest") or {}
    issues = list(context.get("errors") or []) + [
        imp.friendly_relationship_issue(issue)
        for issue in context.get("relationship_issues") or []
    ]
    return {
        "type": action_type,
        "status": "needs_input" if unresolved_count or issues else "ready",
        "template_count": len(manifest.get("templates") or context.get("template_ids") or []),
        "list_count": len(manifest.get("lists") or context.get("list_ids") or []),
        "account_count": len(manifest.get("accounts") or context.get("account_ids") or []),
        "mapping_count": len(context.get("mappings") or []),
        "mapping_rows": context.get("mapping_review_rows") or [],
        "asset_summary": context.get("asset_summary") or {},
        "inferred_name_count": int(context.get("inferred_name_count") or 0),
        "materialized": bool(context.get("materialized")),
        "issues": issues,
        "unresolved_count": unresolved_count,
        "skipped_count": len(skipped_requirements),
        "skipped_categories": sorted({
            str(item.get("category") or item.get("variable") or "value")
            for item in skipped_requirements
        }),
        "variable_groups": groups,
        "import_counts": import_counts or context.get("import_counts"),
    }


def _format_pending_categories(context: Dict[str, Any]) -> str:
    from services import assistant_import_service as imp

    categories = sorted({
        item.get("category") or item.get("variable") or "value"
        for item in imp.unresolved_requirements(context)
    })
    return ", ".join(str(item).replace("_", " ").title() for item in categories)

def _get_user_context(db: Session, user: User) -> str:
    """Build a safe context string with user's high-level stats only."""
    try:
        inbox_count = db.query(Inbox).filter(Inbox.user_id == user.id).count()
        warming_count = db.query(Inbox).filter(
            Inbox.user_id == user.id,
            Inbox.state == InboxState.WARMING_UP
        ).count()
        warmed_count = db.query(Inbox).filter(
            Inbox.user_id == user.id,
            Inbox.state == InboxState.WARMED_UP
        ).count()
        campaign_count = db.query(Campaign).filter(Campaign.user_id == user.id).count()
        active_campaigns = db.query(Campaign).filter(
            Campaign.user_id == user.id,
            Campaign.status.in_([CampaignStatus.RUNNING, CampaignStatus.SCHEDULED])
        ).count()
        list_count = db.query(RecipientList).filter(RecipientList.user_id == user.id).count()
        smtp_count = db.query(SMTPAccount).filter(SMTPAccount.user_id == user.id).count()

        return (
            f"- User: {user.name or 'User'}\n"
            f"- SMTP accounts: {smtp_count}\n"
            f"- Inboxes: {inbox_count} total, {warming_count} warming up, {warmed_count} warmed up\n"
            f"- Campaigns: {campaign_count} total, {active_campaigns} active\n"
            f"- Recipient lists: {list_count}\n"
        )
    except Exception as e:
        logger.error(f"Error building user context: {e}")
        return "- Could not load user stats\n"


@router.post("/chat", response_model=ChatResponse)
def chat_with_assistant(
    req: ChatRequest,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Chat with Fleet Assistant"""
    message_text = req.message.strip()
    if not message_text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    if req.conversation_id is None:
        conversation = create_conversation(
            db,
            current_user.id,
            title=title_from_message(message_text),
        )
    else:
        conversation = get_conversation(db, current_user.id, req.conversation_id)
        if conversation.title == "New conversation":
            conversation.title = title_from_message(message_text)

    add_message(db, conversation, "user", message_text)

    delete_result = handle_delete_intent(db, current_user, conversation, message_text)
    if delete_result is not None:
        reply_text, action = delete_result
        assistant_message = add_message(db, conversation, "assistant", reply_text, action)
        return ChatResponse(
            reply=reply_text,
            conversation_id=conversation.id,
            message_id=assistant_message.id,
            action=action,
        )

    # Custom bundle values are resolved in chat before normal guidance. The
    # model may interpret phrasing, but deterministic code validates scope and
    # stores user-provided values verbatim without opening or validating links.
    try:
        from services import assistant_import_service as _imp

        import_context = _imp.load_import_context(conversation.id)
        if import_context and int(import_context.get("user_id") or current_user.id) == current_user.id:
            variable_result = _imp.resolve_variable_message(import_context, message_text)
            if variable_result is not None:
                _imp.save_import_context(import_context)
                variable_intent = variable_result.get("intent")
                if variable_intent == "confirm_skip_values":
                    categories = variable_result.get("categories") or []
                    category_text = ", ".join(
                        str(item).replace("_", " ").title() for item in categories
                    ) or _format_pending_categories(import_context)
                    reply_text = (
                        f"Yes. I can leave **{category_text}** blank, but those placeholders will render as "
                        "empty text in the emails. Reply **Continue without those values** to make that choice "
                        "and move to the campaign review; drafts will still require final confirmation."
                    )
                    action = _bundle_state_action(import_context, action_type="bundle_variables")
                elif variable_intent == "skip_values":
                    skipped = variable_result.get("skipped") or []
                    skipped_categories = sorted({
                        str(item.get("category") or item.get("variable") or "value")
                        .replace("_", " ").title()
                        for item in skipped
                    })
                    skipped_templates = {
                        str(item.get("template") or "Template") for item in skipped
                    }
                    decision_text = (
                        f"Understood — I left **{len(skipped)} custom value(s)** blank "
                        f"across **{len(skipped_templates)} template(s)**"
                    )
                    if skipped_categories:
                        decision_text += f": **{', '.join(skipped_categories)}**"
                    decision_text += ". Those placeholders will render as empty text."

                    if variable_result.get("proceed"):
                        plan_reply, action = _prepare_campaign_plan(db, current_user, conversation)
                        reply_text = f"{decision_text}\n\n{plan_reply}"
                    elif variable_result.get("remaining"):
                        reply_text = (
                            f"{decision_text} Still needed: **{_format_pending_categories(import_context)}**. "
                            "Provide those values, or explicitly ask me to leave the remaining ones blank too."
                        )
                        action = _bundle_state_action(import_context, action_type="bundle_variables")
                    else:
                        reply_text = (
                            f"{decision_text} The reviewed import is now ready. "
                            "Choose **Approve import & review** to prepare the draft campaign plan."
                        )
                        action = _bundle_state_action(import_context, action_type="bundle_variables")
                elif variable_result.get("needs_category"):
                    reply_text = (
                        "I'm not sure where that value should go, and I don't want to put it in the wrong email. "
                        f"Please tell me which of these it is for: **{_format_pending_categories(import_context)}**. "
                        "For example: `LinkedIn: your link`."
                    )
                elif variable_result.get("scope_requests"):
                    request = variable_result["scope_requests"][0]
                    template_names = ", ".join(request["templates"][:6])
                    reply_text = (
                        f"That value could be used in **{request['count']} emails**: {template_names}. "
                        "Should I use the same value in all of them, or only in specific emails? "
                        "Reply **all**, or tell me the email names."
                    )
                elif variable_result.get("scope_declined"):
                    reply_text = (
                        "Understood. I won't reuse that value. Send a separate value with each email name, "
                        "for example `Alstom LinkedIn: your link`."
                    )
                else:
                    applied_categories = sorted({
                        item.get("category") or item.get("variable") or "value"
                        for item in variable_result["applied"]
                    })
                    applied_label = ", ".join(
                        str(item).replace("_", " ").title() for item in applied_categories
                    )
                    if variable_result["remaining"]:
                        reply_text = (
                            f"Saved **{applied_label}** for {len(variable_result['applied'])} email(s). "
                            f"I still need: **{_format_pending_categories(import_context)}**. "
                            "Send each one like `LinkedIn: your link`. If the same value belongs in several emails, "
                            "say **all** or name those emails."
                        )
                    else:
                        reply_text = (
                            f"Saved **{applied_label}** for {len(variable_result['applied'])} email(s). "
                            "Everything I need is now filled in, so the import is ready for your approval."
                        )
                if variable_intent not in {"confirm_skip_values", "skip_values"}:
                    action = _bundle_state_action(import_context, action_type="bundle_variables")
                assistant_message = add_message(db, conversation, "assistant", reply_text, action)
                return ChatResponse(
                    reply=reply_text,
                    conversation_id=conversation.id,
                    message_id=assistant_message.id,
                    action=action,
                )
            mapping_result = _imp.resolve_mapping_message(import_context, message_text)
            if mapping_result is not None:
                _imp.save_import_context(import_context)
                reply_text = (
                    f"Got it. I'll use **{mapping_result['template']}** for **{mapping_result['list']}**, "
                    f"sent from **{mapping_result['account']}**."
                )
                if mapping_result["remaining_issues"]:
                    next_issue = _imp.friendly_relationship_issue(mapping_result["remaining_issues"][0])
                    reply_text += f" I still need help with one more choice: {next_issue}"
                elif _imp.unresolved_requirements(import_context):
                    reply_text += f" Before I continue, I still need: **{_format_pending_categories(import_context)}**."
                else:
                    reply_text += " Everything is matched now, so the import is ready for your approval."
                action = _bundle_state_action(import_context)
                assistant_message = add_message(db, conversation, "assistant", reply_text, action)
                return ChatResponse(
                    reply=reply_text,
                    conversation_id=conversation.id,
                    message_id=assistant_message.id,
                    action=action,
                )
    except Exception as exc:
        logger.warning("Bundle variable resolution skipped: %s", exc)

    api_key = os.getenv("GEMINI_API_KEY", "")
    from services import llm_provider
    if not llm_provider.is_configured():
        raise HTTPException(status_code=503, detail="Assistant is not configured.")

    try:
        # Build system prompt with user context
        user_context = _get_user_context(db, current_user)
        system = SYSTEM_PROMPT.replace("{user_context}", user_context)

        # Surface this conversation's bundle import so the assistant can offer
        # to reuse those templates/lists when the user later asks to create
        # campaigns in the same chat.
        try:
            from services import assistant_import_service as _imp
            _import_ctx = _imp.load_import_context(conversation.id)
            if _import_ctx:
                from models.ses_template import SESEmailTemplate as _Tpl
                from models.list import RecipientList as _Rl
                if _import_ctx.get("materialized"):
                    tpl_names = [
                        t.name for t in db.query(_Tpl).filter(
                            _Tpl.id.in_(_import_ctx.get("template_ids") or []),
                            _Tpl.user_id == current_user.id,
                        ).all()
                    ]
                    list_names = [
                        l.name for l in db.query(_Rl).filter(
                            _Rl.id.in_(_import_ctx.get("list_ids") or []),
                            _Rl.user_id == current_user.id,
                        ).all()
                    ]
                else:
                    manifest = _import_ctx.get("manifest") or {}
                    tpl_names = [item.get("name", "") for item in manifest.get("templates") or []]
                    list_names = [item.get("name", "") for item in manifest.get("lists") or []]
                if tpl_names or list_names:
                    unresolved_count = len(_imp.unresolved_requirements(_import_ctx))
                    system += (
                        "\n\nIMPORT CONTEXT — this conversation previously imported a bundle: "
                        f"templates: {', '.join(tpl_names[:12]) or 'none'}; "
                        f"lists: {', '.join(list_names[:12]) or 'none'}. "
                        f"unresolved custom values: {unresolved_count}. "
                        "If the user asks to create campaigns, offer to use THESE templates and lists first. "
                        "Do not claim campaign preparation is ready while unresolved custom values remain."
                    )
        except Exception:
            pass

        # Build normalized history from the database so it survives page and
        # server restarts.
        contents = [
            {
                "role": "user" if message.role == "user" else "assistant",
                "content": message.content or "",
            }
            for message in get_recent_messages(db, conversation.id, limit=30)
        ]

        MAX_TOOL_HOPS = 4
        tool_call_counter = 0

        for _hop in range(MAX_TOOL_HOPS):
            result = llm_provider.chat(
                system=system,
                messages=contents,
                tools=TOOL_DECLARATIONS,
                max_tokens=500,
            )

            if not result.tool_calls:
                reply_text = result.text or "I'm sorry, I couldn't process that. Please try again."
                assistant_message = add_message(db, conversation, "assistant", reply_text)
                return ChatResponse(
                    reply=reply_text,
                    conversation_id=conversation.id,
                    message_id=assistant_message.id,
                )

            call = result.tool_calls[0]
            if call.name == "prepare_campaigns":
                # Campaign generation from this conversation's bundle import.
                reply_text, action = _prepare_campaign_plan(db, current_user, conversation)
                assistant_message = add_message(db, conversation, "assistant", reply_text, action)
                return ChatResponse(
                    reply=reply_text,
                    conversation_id=conversation.id,
                    message_id=assistant_message.id,
                    action=action,
                )

            spec = TOOL_REGISTRY.get(call.name)
            if spec is None:
                result_str = f"Unknown tool '{call.name}'."
            else:
                selector = (call.args or {}).get("selector", "")
                resource_id, _label, short_circuit = resolve_single_resource(
                    db, current_user.id, spec.resource_type, selector,
                )
                if short_circuit is not None:
                    reply_text, action = short_circuit
                    if action is not None:
                        action["tool_name"] = call.name
                    assistant_message = add_message(db, conversation, "assistant", reply_text, action)
                    return ChatResponse(
                        reply=reply_text,
                        conversation_id=conversation.id,
                        message_id=assistant_message.id,
                        action=action,
                    )
                result_str = spec.run(db, current_user, resource_id)

            tool_call_id = f"call_{tool_call_counter}"
            tool_call_counter += 1
            contents.append({
                "role": "assistant",
                "tool_calls": [{"id": tool_call_id, "name": call.name, "args": dict(call.args or {})}],
                "reasoning_content": result.reasoning_content,
            })
            contents.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": call.name,
                "content": result_str,
            })
        else:
            # Hop cap reached without a natural stopping point — force one
            # final plain-text answer with tools disabled rather than loop forever.
            result = llm_provider.chat(
                system=system,
                messages=contents,
                tools=None,
                max_tokens=500,
            )
            reply_text = result.text or "I've done what I can with the information so far."
            assistant_message = add_message(db, conversation, "assistant", reply_text)
            return ChatResponse(
                reply=reply_text,
                conversation_id=conversation.id,
                message_id=assistant_message.id,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Fleet Assistant error: {e}")
        raise HTTPException(status_code=500, detail="Assistant encountered an error. Please try again.")


# ── Bundle import + campaign generation ────────────────────────────────

@router.post("/import-bundle")
async def import_bundle(
    request: Request,
    file: UploadFile = File(...),
    conversation_id: Optional[int] = Form(None),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Import a zip bundle: accounts + lists + templates (+attachments)."""
    from services import assistant_import_service as imp

    if conversation_id is None:
        query_conversation_id = request.query_params.get("conversation_id")
        if query_conversation_id:
            try:
                conversation_id = int(query_conversation_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid conversation_id")

    raw = await file.read()
    try:
        files = imp.unzip_bundle(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not files:
        raise HTTPException(status_code=400, detail="Bundle is empty")

    if conversation_id is None:
        conversation = create_conversation(db, current_user.id, title="Bundle import")
    else:
        conversation = get_conversation(db, current_user.id, conversation_id)

    add_message(db, conversation, "user", f"Uploaded bundle for review: {file.filename}")

    try:
        summary = imp.stage_import(db, current_user, files, conversation.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logger.exception("Bundle analysis failed for user %s", current_user.id)
        raise HTTPException(
            status_code=500,
            detail="Bundle analysis failed safely; no resources were created. Please try again.",
        )

    lines = [
        f"✅ Analyzed **{file.filename}** without creating anything:",
        f"- Sending accounts: {summary['accounts_created']} new, {summary['accounts_reused']} reusable, {summary['accounts_updated']} to update, {summary['accounts_failed']} failed",
        f"- Inboxes: {summary['inboxes_created']} new, {summary['inboxes_reused']} reusable, {summary['inboxes_updated']} to update, {summary['inboxes_failed']} failed",
        f"- Lists: {summary['lists_created']} new, {summary['lists_reused']} reusable, {summary['lists_updated']} to update, {summary['lists_failed']} failed",
        f"- Templates: {summary['templates_created']} new, {summary['templates_reused']} reusable, {summary['templates_updated']} to update, {summary['templates_failed']} failed",
    ]
    if summary.get("bundle_reused"):
        lines.append("- Identical bundle fingerprint found; its existing plan was reused")
    if summary["orphans"]:
        lines.append(f"- ⚠️ {len(summary['orphans'])} file(s) I couldn't classify: {', '.join(summary['orphans'][:5])}")
    if summary["errors"]:
        lines.append(f"- ⚠️ {len(summary['errors'])} error(s): {summary['errors'][0][:120]}")
    reply_text = "\n".join(lines)

    action = None
    context = imp.load_import_context(conversation.id)
    if context:
        relationship_issues = context.get("relationship_issues") or []
        if relationship_issues:
            friendly_issue = imp.friendly_relationship_issue(relationship_issues[0])
            lines.append("- ⚠️ I need your help with one choice before I continue.")
            lines.append(f"- {friendly_issue}")
            if "sender" in friendly_issue.casefold():
                lines.append(
                    "- You can reply: `Use [email template] for [contact list] with [sending account]`."
                )
            else:
                lines.append("- You can reply: `Use [email template] for [contact list]`.")
        counts = context.get("import_counts") or summary.get("import_counts") or {
            "accounts": {"created": summary["accounts_created"], "reused": summary["accounts_reused"], "updated": summary["accounts_updated"], "failed": summary["accounts_failed"]},
            "inboxes": {"created": summary["inboxes_created"], "reused": summary["inboxes_reused"], "updated": summary["inboxes_updated"], "failed": summary["inboxes_failed"]},
            "lists": {"created": summary["lists_created"], "reused": summary["lists_reused"], "updated": summary["lists_updated"], "failed": summary["lists_failed"]},
            "templates": {"created": summary["templates_created"], "reused": summary["templates_reused"], "updated": summary["templates_updated"], "failed": summary["templates_failed"]},
        }
        action = _bundle_state_action(context, counts)
        if summary["unresolved_count"]:
            lines.append(
                f"- Before I can finish, I still need {summary['unresolved_count']} detail(s): "
                f"{_format_pending_categories(context)}. Send each one like `LinkedIn: your link`."
            )
        elif relationship_issues:
            lines.append("- I haven't created or sent anything. I'll continue after you confirm that choice.")
        else:
            lines.append(f"- All {len(summary['mappings'])} planned campaigns are matched and ready for your approval")
        reply_text = "\n".join(lines)

    assistant_message = add_message(db, conversation, "assistant", reply_text, action)
    return {
        "reply": reply_text,
        "conversation_id": conversation.id,
        "message_id": assistant_message.id,
        "action": action,
    }


@router.post("/campaigns/prepare")
def prepare_campaigns(
    request: CampaignsActionRequest,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Build the campaign-generation plan from this conversation's import context."""
    conversation = get_conversation(db, current_user.id, request.conversation_id)
    reply_text, action = _prepare_campaign_plan(db, current_user, conversation)
    m = add_message(db, conversation, "assistant", reply_text, action)
    return {"reply": reply_text, "conversation_id": conversation.id, "message_id": m.id, "action": action}


def _prepare_campaign_plan(
    db: Session,
    user: User,
    conversation,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Build (and persist) the campaign plan for a conversation's import.

    Returns (reply_text, action) where action is the plan card, or
    (reply_text, None) when there is no import context in this conversation.
    """
    import json as _json

    from services import assistant_import_service as imp

    context = imp.load_import_context(conversation.id)
    if not context or not ((context.get("manifest") or {}).get("templates") or context.get("template_ids")) or not ((context.get("manifest") or {}).get("lists") or context.get("list_ids")):
        reply_text = (
            "There's no bundle import in this conversation yet. "
            "Upload a bundle zip (accounts + lists + templates) and I'll create the campaigns for you."
        )
        return reply_text, None
    if int(context.get("user_id") or user.id) != user.id:
        return "I couldn't use that import context. Please upload the bundle again in this conversation.", None

    errors = list(context.get("errors") or [])
    if errors:
        reply_text = (
            "I couldn't finish checking the bundle because one file needs attention: "
            f"**{errors[0]}**. Nothing has been created or sent."
        )
        return reply_text, _bundle_state_action(context)

    relationship_issues = list(context.get("relationship_issues") or [])
    if relationship_issues:
        friendly_issue = imp.friendly_relationship_issue(relationship_issues[0])
        example = (
            "`Use [email template] for [contact list] with [sending account]`"
            if "sender" in friendly_issue.casefold()
            else "`Use [email template] for [contact list]`"
        )
        reply_text = (
            f"I paused because I don't want to make the wrong choice. **{friendly_issue}** "
            f"You can reply with the names, for example: {example}. "
            "I won't prepare the campaigns until you confirm it."
        )
        return reply_text, _bundle_state_action(context)

    unresolved = imp.unresolved_requirements(context)
    if unresolved:
        reply_text = (
            f"Before I can prepare the campaigns, I still need **{len(unresolved)} detail(s)**: "
            f"**{_format_pending_categories(context)}**. Send each one like `LinkedIn: your link`. "
            "If a value belongs in several emails, say **all**; otherwise, include the email name."
        )
        return reply_text, _bundle_state_action(context, action_type="bundle_variables")

    materialize_summary = None
    if not context.get("materialized"):
        try:
            materialize_summary, context = imp.materialize_staged_import(db, user, context)
        except ValueError as exc:
            return f"I couldn't apply the reviewed import plan: {exc}.", _bundle_state_action(context)
        if materialize_summary.get("errors"):
            return (
                "I applied the import plan, but some resources need attention: "
                f"{materialize_summary['errors'][0]}",
                _bundle_state_action(context),
            )

    from models.ses_template import SESEmailTemplate

    templates = db.query(SESEmailTemplate).filter(
        SESEmailTemplate.id.in_(context["template_ids"]), SESEmailTemplate.user_id == user.id
    ).all()
    lists = db.query(RecipientList).filter(
        RecipientList.id.in_(context["list_ids"]), RecipientList.user_id == user.id
    ).all()
    inboxes = db.query(Inbox).filter(
        Inbox.id.in_(context.get("inbox_ids") or []), Inbox.user_id == user.id
    ).all()
    if not inboxes:
        # Fallback: any active inbox owned by the user — new inboxes send at
        # ACTIVE pace from day 0, so warmed-up is not required.
        inboxes = db.query(Inbox).filter(
            Inbox.user_id == user.id,
            Inbox.is_active == True,
        ).order_by(Inbox.id).all()
    templates_by_id = {item.id: item for item in templates}
    lists_by_id = {item.id: item for item in lists}
    inboxes_by_id = {item.id: item for item in inboxes}

    plan = []
    for mapping in context.get("mappings") or []:
        try:
            template = templates_by_id.get(int(mapping.get("template_id")))
            recipient_list = lists_by_id.get(int(mapping.get("list_id")))
        except (TypeError, ValueError):
            continue
        if not template or not recipient_list:
            continue
        mapped_inbox_ids = [
            int(inbox_id) for inbox_id in mapping.get("inbox_ids") or []
            if int(inbox_id) in inboxes_by_id
        ]
        if not mapped_inbox_ids:
            mapped_inbox_ids = list(inboxes_by_id)
        plan.append({
            "template": template,
            "list": recipient_list,
            "inbox_ids": mapped_inbox_ids,
            "send_timezone": mapping.get("send_timezone") or "US/Eastern",
            "start": bool(mapping.get("start")),
        })

    # Contexts created before the smart manifest remain fully compatible.
    if not plan:
        plan = imp.build_campaign_plan(
            templates,
            lists,
            inboxes,
            context.get("automation_rows") or None,
        )
    if not plan:
        reply_text = "I couldn't build a plan — I need at least one template and one list."
        return reply_text, None

    plan_payload = [
        {
            "template_id": row["template"].id,
            "list_id": row["list"].id,
            "inbox_ids": row["inbox_ids"],
            "send_timezone": row["send_timezone"],
            "start": row["start"],
            "template_data": (context.get("template_values") or {}).get(str(row["template"].id), {}),
            "bundle_hash": context.get("bundle_hash"),
        }
        for row in plan
    ]
    try:
        r = get_redis_client()
        r.setex(
            f"assistant:conv:{conversation.id}:plan",
            1800,
            _json.dumps(plan_payload),
        )
    except Exception as exc:
        logger.error("Failed to persist assistant campaign plan: %s", exc)
        return "I couldn't save the campaign plan. Please try again.", None

    rows = [
        {
            "template": row["template"].name,
            "list": row["list"].name,
            "recipients": row["list"].recipient_count or 0,
            "inboxes": len(row["inbox_ids"]),
        }
        for row in plan
    ]
    reply_text = (
        f"Here's my plan — **{len(plan)} campaign(s)**, one per list. "
        "The bundle relationships and custom-value choices are locked in; templates may be reused, but lists are not."
    )
    skipped_requirements = [item for item in context.get("requirements") or [] if item.get("skipped")]
    skipped_categories = sorted({
        str(item.get("category") or item.get("variable") or "value")
        for item in skipped_requirements
    })
    if skipped_requirements:
        reply_text += (
            f" **{len(skipped_requirements)} explicitly skipped value(s)** will remain blank "
            f"({', '.join(item.replace('_', ' ').title() for item in skipped_categories)})."
        )
    if materialize_summary is not None:
        counts = context.get("import_counts") or {}
        summary_rows = []
        for label, key in (("Accounts", "accounts"), ("Inboxes", "inboxes"), ("Lists", "lists"), ("Templates", "templates")):
            row = counts.get(key) or {}
            summary_rows.append(
                f"- {label}: {int(row.get('created') or 0)} / {int(row.get('reused') or 0)} / "
                f"{int(row.get('updated') or 0)} / {int(row.get('failed') or 0)}"
            )
        reply_text += "\n\n**Operational summary — created / reused / updated / failed**\n" + "\n".join(summary_rows)
    action = {
        "type": "create_campaigns_plan",
        "status": "pending",
        "rows": rows,
        "skipped_count": len(skipped_requirements),
        "skipped_categories": skipped_categories,
    }
    return reply_text, action


@router.post("/campaigns/confirm")
def confirm_campaigns(
    request: CampaignsActionRequest,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Execute the prepared campaign-generation plan."""
    import json as _json
    from models.ses_template import SESEmailTemplate
    from services import assistant_import_service as imp
    from services.campaign_service import CampaignService

    conversation = get_conversation(db, current_user.id, request.conversation_id)
    context = imp.load_import_context(conversation.id)
    if not context or int(context.get("user_id") or current_user.id) != current_user.id:
        raise HTTPException(status_code=400, detail="Bundle import context is missing. Upload the bundle again.")
    if imp.unresolved_requirements(context):
        raise HTTPException(status_code=400, detail="Required template values are still unresolved.")
    r = get_redis_client()
    raw = r.get(f"assistant:conv:{conversation.id}:plan")
    if not raw:
        raise HTTPException(status_code=400, detail="No pending campaign plan. Run prepare first.")
    plan_payload = _json.loads(raw)
    fresh_values = context.get("template_values") or {}
    for row in plan_payload:
        row["template_data"] = dict(fresh_values.get(str(row.get("template_id"))) or {})

    service = CampaignService(db)
    created: List[int] = []
    reused: List[int] = []
    failed: List[str] = []

    for row in plan_payload:
        try:
            if not row.get("inbox_ids"):
                failed.append(f"list {row.get('list_id')}: no inboxes available — add inboxes first")
                continue
            template = db.query(SESEmailTemplate).filter(
                SESEmailTemplate.id == row["template_id"],
                SESEmailTemplate.user_id == current_user.id,
            ).first()
            rlist = db.query(RecipientList).filter(
                RecipientList.id == row["list_id"],
                RecipientList.user_id == current_user.id,
            ).first()
            if not template or not rlist:
                failed.append(f"missing template/list for {row}")
                continue
            owned_inbox_ids = {
                item.id for item in db.query(Inbox).filter(
                    Inbox.id.in_(row["inbox_ids"]),
                    Inbox.user_id == current_user.id,
                ).all()
            }
            if owned_inbox_ids != set(row["inbox_ids"]):
                failed.append(f"list {rlist.id}: one or more inboxes are unavailable")
                continue

            campaign_name = f"{template.name} → {rlist.name}"
            desired_import_hash = _campaign_import_hash(row)
            existing_campaigns = db.query(Campaign).filter(
                Campaign.user_id == current_user.id,
                Campaign.list_id == rlist.id,
                Campaign.status == CampaignStatus.DRAFT,
            ).all()
            desired_template_data = row.get("template_data") or {}
            duplicate = next((
                campaign for campaign in existing_campaigns
                if (campaign.template_data or {}).get("_assistant_import_hash") == desired_import_hash
                or (
                    not (campaign.template_data or {}).get("_assistant_import_hash")
                    and (campaign.template_data or {}).get("_assistant_bundle_hash") == row.get("bundle_hash")
                    and campaign.name == campaign_name
                    and template.id in (campaign.template_ids or [])
                    and sorted(campaign.inbox_ids or []) == sorted(row["inbox_ids"])
                )
            ), None)
            if duplicate is not None:
                duplicate.template_data = {
                    **desired_template_data,
                    "_assistant_bundle_hash": row.get("bundle_hash"),
                    "_assistant_import_hash": desired_import_hash,
                }
                db.commit()
                reused.append(duplicate.id)
                continue
            campaign = service.create_campaign(
                name=campaign_name,
                subject=template.subject_line or template.name,
                body_html=template.html_content or "<p></p>",
                list_id=rlist.id,
                inbox_ids=row["inbox_ids"] or [],
                body_text=template.text_fallback,
                track_opens=True,
                track_clicks=True,
                user_id=current_user.id,
                attachments=template.attachments,
                template_ids=[template.id],
                send_timezone=row.get("send_timezone") or "US/Eastern",
                template_data={
                    **(row.get("template_data") or {}),
                    "_assistant_bundle_hash": row.get("bundle_hash"),
                    "_assistant_import_hash": desired_import_hash,
                },
            )
            created.append(campaign.id)
        except Exception as e:
            db.rollback()
            failed.append(f"{row.get('list_id')}: {e}")

    r.delete(f"assistant:conv:{conversation.id}:plan")

    reply_text = f"✅ Created **{len(created)} campaign(s)**"
    reply_text += " as drafts"
    if reused:
        reply_text += f"; {len(reused)} existing duplicate(s) reused"
    if failed:
        reply_text += f"; {len(failed)} failed"
    reply_text += ". You can review them on the Campaigns page."
    action = {
        "type": "create_campaigns_plan",
        "status": "completed",
        "created": len(created),
        "reused": len(reused),
        "failed": len(failed),
    }
    m = add_message(db, conversation, "assistant", reply_text, action)
    return {"reply": reply_text, "conversation_id": conversation.id, "message_id": m.id, "action": action}


@router.get("/conversations")
def get_conversations(
    limit: int = 50,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """List the signed-in user's resumable conversations."""
    conversations = list_conversations(db, current_user.id, limit=limit)
    return {"conversations": [serialize_conversation_summary(item) for item in conversations]}


@router.post("/conversations")
def start_conversation(
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Create a new empty conversation."""
    conversation = create_conversation(db, current_user.id)
    return serialize_conversation_summary(conversation)


@router.get("/conversations/{conversation_id}/bundle-template-previews")
def get_bundle_template_previews(
    conversation_id: int,
    category: str,
    template_id: Optional[int] = None,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """List or load staged templates that require a categorized custom value."""
    from services import assistant_import_service as imp

    get_conversation(db, current_user.id, conversation_id)
    context = imp.load_import_context(conversation_id)
    if not context or int(context.get("user_id") or 0) != current_user.id:
        raise HTTPException(status_code=404, detail="Bundle preview expired. Upload the bundle again.")

    templates = imp.variable_template_previews(context, category, template_id)
    if not templates:
        raise HTTPException(status_code=404, detail="No matching template preview was found.")
    return {
        "category": imp.canonical_variable_name(category),
        "templates": templates,
    }


@router.get("/conversations/{conversation_id}")
def get_conversation_messages(
    conversation_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Load a conversation and all of its persisted messages."""
    conversation = get_conversation(db, current_user.id, conversation_id)
    return {
        "conversation": serialize_conversation_summary(conversation),
        "messages": [serialize_message(db, message) for message in conversation.messages],
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation_history(
    conversation_id: int,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Archive a conversation from the user's history."""
    archive_conversation(db, current_user.id, conversation_id)
    return {"success": True}


@router.post("/actions/prepare-delete", response_model=ActionResponse)
def prepare_assistant_delete(
    request: PrepareDeleteRequest,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Prepare owned resource deletions and require confirmation."""
    conversation = get_conversation(db, current_user.id, request.conversation_id)
    reply, action = prepare_delete_action(
        db,
        current_user.id,
        conversation,
        request.resource_type,
        request.resource_ids,
    )
    message = add_message(db, conversation, "assistant", reply, action)
    return ActionResponse(
        reply=reply,
        conversation_id=conversation.id,
        message_id=message.id,
        action=action,
    )


@router.post("/actions/confirm", response_model=ActionResponse)
def confirm_assistant_action(
    request: ActionIdsRequest,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Execute previously prepared owned deletions."""
    pending_rows = db.query(AssistantPendingAction).filter(
        AssistantPendingAction.id.in_(request.action_ids),
        AssistantPendingAction.user_id == current_user.id,
    ).all()
    if not pending_rows:
        raise HTTPException(status_code=404, detail="Delete confirmation not found")
    conversation = get_conversation(db, current_user.id, pending_rows[0].conversation_id)
    reply, action = confirm_delete_actions(db, current_user, request.action_ids)
    message = add_message(db, conversation, "assistant", reply, action)
    return ActionResponse(
        reply=reply,
        conversation_id=conversation.id,
        message_id=message.id,
        action=action,
    )


@router.post("/actions/cancel", response_model=ActionResponse)
def cancel_assistant_action(
    request: ActionIdsRequest,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Cancel pending deletions."""
    pending_rows = db.query(AssistantPendingAction).filter(
        AssistantPendingAction.id.in_(request.action_ids),
        AssistantPendingAction.user_id == current_user.id,
    ).all()
    if not pending_rows:
        raise HTTPException(status_code=404, detail="Delete confirmation not found")
    conversation = get_conversation(db, current_user.id, pending_rows[0].conversation_id)
    reply, action = cancel_delete_actions(db, current_user, request.action_ids)
    message = add_message(db, conversation, "assistant", reply, action)
    return ActionResponse(
        reply=reply,
        conversation_id=conversation.id,
        message_id=message.id,
        action=action,
    )


@router.post("/actions/run-tool", response_model=ChatResponse)
def run_assistant_tool(
    request: RunToolRequest,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Execute a safe (no-confirmation) tool against one or more resolved resources —
    used after the user picks candidate(s) from an ambiguous-selector picker."""
    conversation = get_conversation(db, current_user.id, request.conversation_id)
    spec = TOOL_REGISTRY.get(request.tool_name)
    if spec is None or spec.requires_confirmation:
        raise HTTPException(status_code=400, detail="Unsupported tool")

    results: List[str] = []
    for resource_id in request.resource_ids:
        try:
            get_owned_resource(db, current_user.id, spec.resource_type, resource_id)
            results.append(spec.run(db, current_user, resource_id))
        except HTTPException as exc:
            results.append(str(exc.detail))
        except Exception as exc:
            logger.warning("Tool %s failed for resource %s: %s", request.tool_name, resource_id, exc)
            results.append("An unexpected error occurred.")

    reply_text = "\n".join(results) if results else "Nothing to run."
    message = add_message(db, conversation, "assistant", reply_text)
    return ChatResponse(
        reply=reply_text,
        conversation_id=conversation.id,
        message_id=message.id,
    )
