"""Assistant bundle import — zip → accounts, inboxes, lists, templates (+attachments).

Pipeline:
  1. Unzip the bundle.
  2. Deterministic classification (extensions, folder names, base-name pairing).
  3. AI classification for whatever deterministic rules couldn't place.
    4. AI extraction of Resend accounts from any text/image/pdf account file.
    5. Create Resend accounts → inboxes → lists → templates, storing per-conversation
     context so the assistant can later generate campaigns from them.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import time
import uuid
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from models.inbox import Inbox
from models.list import RecipientList, RecipientStatus
from models.recipient import Recipient
from models.ses_template import SESEmailTemplate
from models.smtp_account import SMTPAccount, EncryptionType
from models.user import User
from services.attachment_service import resolve_attachment_path, store_attachment
from services.redis_client import get_redis_client
from services.template_engine import TemplateEngine, infer_recipient_name

logger = logging.getLogger(__name__)

UPLOAD_DIR = os.environ.get(
    "UPLOADS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads"),
)
os.makedirs(UPLOAD_DIR, exist_ok=True)
STAGING_DIR = os.path.join(UPLOAD_DIR, ".assistant-staging")
os.makedirs(STAGING_DIR, exist_ok=True)

BODY_EXTENSIONS = {".html", ".htm", ".txt"}
ATTACH_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".zip", ".rar", ".csv",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
ALLOWED_BUNDLE_EXTENSIONS = BODY_EXTENSIONS | ATTACH_EXTENSIONS | {".csv", ".json"}

MAX_ZIP_BYTES = 100 * 1024 * 1024  # 100MB
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_AI_FILES = 30

SUBJECT_RE = re.compile(r"^subject:\s*(.*)$", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

CONTEXT_KEY = "assistant:conv:{conversation_id}:import"
BUNDLE_CONTEXT_KEY = "assistant:user:{user_id}:bundle:{bundle_hash}"
CONTEXT_TTL = 30 * 24 * 3600  # 30 days
IMPORT_VERSION = 3

AUTOMATIC_VARIABLES = {
    "first_name", "firstname", "last_name", "lastname", "full_name", "fullname",
    "client_name", "clientname", "recipient_name", "recipientname", "email",
    "company", "title", "sender_name", "sendername", "sender_email", "senderemail",
    "unsubscribe_link", "pdf_filename",
}

VARIABLE_ALIASES = {
    "first name": "first_name",
    "firstname": "first_name",
    "client first name": "first_name",
    "recipient first name": "first_name",
    "last name": "last_name",
    "lastname": "last_name",
    "surname": "last_name",
    "full name": "full_name",
    "fullname": "full_name",
    "client name": "full_name",
    "recipient name": "full_name",
    "linkedin link": "linkedin",
    "linkedin links": "linkedin",
    "linkedin url": "linkedin",
    "linkedin urls": "linkedin",
    "agreement name": "agreement_name",
    "agreement names": "agreement_name",
    "microsoft agreement link": "microsoft_esignature_agreement_link",
    "microsoft esignature link": "microsoft_esignature_agreement_link",
    "microsoft esignature agreement link": "microsoft_esignature_agreement_link",
    "microsoft links": "microsoft_esignature_agreement_link",
    "authorized sender": "sender_name",
    "authorized sender name": "sender_name",
    "pdf file name": "pdf_filename",
    "pdf filename": "pdf_filename",
    "reporting period": "reporting_period",
    "reporting periods": "reporting_period",
    "coverage period": "coverage_period",
    "coverage periods": "coverage_period",
    "application names": "application",
}


# ═════════════════════════════════════════════════════════════════════════
# Bundle unzip
# ═════════════════════════════════════════════════════════════════════════

def unzip_bundle(content: bytes) -> Dict[str, bytes]:
    """Unzip an import bundle into {filename: bytes}. Raises ValueError on bad zips."""
    if len(content) > MAX_ZIP_BYTES:
        raise ValueError(f"Bundle too large (max {MAX_ZIP_BYTES // 1024 // 1024}MB)")
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise ValueError("Not a valid zip file")
    extracted: Dict[str, bytes] = {}
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/")
        if name.startswith("/"):
            raise ValueError("Bundle contains an unsafe file path")
        while name.startswith("./"):
            name = name[2:]
        parts = [part for part in name.split("/") if part]
        if not parts or any(part == ".." for part in parts):
            raise ValueError("Bundle contains an unsafe file path")
        name = "/".join(parts)
        if "__MACOSX" in parts or name.endswith(".DS_Store"):
            continue
        raw = zf.read(info)
        if len(raw) > MAX_FILE_BYTES:
            continue
        if _safe_name(name).startswith("."):
            continue
        if name in extracted:
            raise ValueError(f"Bundle contains duplicate file path: {name}")
        extracted[name] = raw

    # Finder and other archive tools commonly wrap everything in one folder
    # (for example ``my-bundle (7)/accounts.txt``). Strip that shared wrapper
    # so every downstream classifier sees the same canonical paths regardless
    # of how the ZIP was created.
    first_parts = {name.split("/", 1)[0] for name in extracted}
    has_shared_wrapper = (
        len(first_parts) == 1
        and bool(extracted)
        and all("/" in name for name in extracted)
        and next(iter(first_parts)).casefold() not in {"lists", "templates"}
    )
    files: Dict[str, bytes] = {}
    for original_name, raw in extracted.items():
        name = original_name.split("/", 1)[1] if has_shared_wrapper else original_name
        if name in files:
            raise ValueError(f"Bundle contains duplicate normalized file path: {name}")
        files[name] = raw
    return files


# ═════════════════════════════════════════════════════════════════════════
# Parsing helpers
# ═════════════════════════════════════════════════════════════════════════

def _safe_name(filename: str) -> str:
    return os.path.basename(filename or "")


def _stem(filename: str) -> str:
    return os.path.splitext(_safe_name(filename))[0].strip().lower()


def _decode(content: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _compact_text_sample(text: str, limit: int = 2400) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    head_size = int(limit * 0.65)
    return text[:head_size] + "\n…[middle omitted]…\n" + text[-(limit - head_size):]


def _extract_text_from_bytes(filename: str, content: bytes) -> str:
    """Best-effort text extraction for the accounts file."""
    ext = os.path.splitext(filename)[1].lower()
    if ext in {".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".html", ".htm"}:
        return _decode(content)
    if ext == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            pages = []
            for page in reader.pages:
                try:
                    pages.append(page.extract_text() or "")
                except Exception:
                    continue
            text = "\n".join(pages).strip()
            if text:
                return text
        except Exception as e:
            logger.warning(f"pypdf extraction failed for {filename}: {e}")
    return ""  # caller may fall back to the vision model


def _extract_json_from_text(text: str) -> Optional[Any]:
    """Pull the first JSON object/array out of an LLM response."""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    candidate = fence.group(1) if fence else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    start = min(
        [i for i in (candidate.find("{"), candidate.find("[")) if i != -1],
        default=-1,
    )
    if start == -1:
        return None
    end = max(candidate.rfind("}"), candidate.rfind("]"))
    if end <= start:
        return None
    try:
        return json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None


def _parse_subject_and_body(text: str) -> Tuple[str, str]:
    lines = text.splitlines()
    subject = None
    body_start = None
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        m = SUBJECT_RE.match(line.strip())
        if m:
            subject = m.group(1).strip()
            body_start = i + 1
            break
    if subject is None:
        subject = ""
        body_start = 0
    body = "\n".join(lines[body_start:]).strip()
    return subject, body


def _identity(value: str) -> str:
    """Stable identity used for duplicate and relationship matching."""
    value = re.sub(r"\s*\(\d+\)\s*$", "", str(value or "").strip())
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _relationship_name_markers(value: str) -> set[str]:
    """Return exact and date-neutral identities for campaign relationship matching."""
    raw = re.sub(r"\s*\(\d+\)\s*$", "", str(value or "").strip())
    markers = {_identity(raw)} if raw else set()
    stem = re.sub(r"\.(?:txt|html?|md|docx?|pdf|csv)$", "", raw, flags=re.IGNORECASE)
    stable_stem = re.sub(
        r"(?:[\s_.-]+)(?:19|20)\d{2}(?:[\s_.-]?(?:0[1-9]|1[0-2]))"
        r"(?:[\s_.-]?(?:0[1-9]|[12]\d|3[01]))$",
        "",
        stem,
    )
    if stable_stem != stem:
        markers.add(_identity(stable_stem))
    return {marker for marker in markers if marker}


def _template_relationship_issue(list_name: Any) -> str:
    list_label = str(list_name or "this contact list")
    return (
        f'I couldn\'t confidently tell which email template should be used for the "{list_label}" '
        "contact list. Please tell me the correct template before I continue."
    )


def _sending_account_relationship_issue(template_name: Any, list_name: Any = None) -> str:
    template_label = str(template_name or "this email")
    if list_name:
        return (
            f'I matched the "{template_label}" email with the "{list_name}" contact list, but I couldn\'t '
            "confidently identify the sender. Please choose the sending account so I don't use the wrong address."
        )
    return (
        f'I couldn\'t confidently identify the sender for the "{template_label}" email. '
        "Please choose the sending account so I don't use the wrong address."
    )


def friendly_relationship_issue(issue: Any) -> str:
    """Convert current or legacy relationship blockers into user-facing language."""
    text = str(issue or "").strip().rstrip(".")
    account_match = re.fullmatch(
        r"Confirm the sending account for template (.+)",
        text,
        flags=re.IGNORECASE,
    )
    if account_match:
        return _sending_account_relationship_issue(account_match.group(1))
    template_match = re.fullmatch(
        r"Confirm which template belongs to (?:list )?(.+)",
        text,
        flags=re.IGNORECASE,
    )
    if template_match:
        return _template_relationship_issue(template_match.group(1))
    if text.casefold() == "one or more recipient lists do not have a campaign template":
        return (
            "I couldn't find an email template for one or more contact lists. "
            "Please tell me which template each list should use before I continue."
        )
    if text.casefold().startswith("semantic review:"):
        text = text.split(":", 1)[1].strip()
    if text.startswith("I "):
        return text + ("" if text.endswith((".", "!", "?")) else ".")
    return f"I need your help confirming one detail before I continue: {text}."


def _mapping_account_label(mapping: Dict[str, Any], accounts_by_id: Dict[int, Dict[str, Any]]) -> str:
    account = accounts_by_id.get(int(mapping.get("account_id") or 0), {})
    if account.get("source_name"):
        return str(account["source_name"])
    if mapping.get("inbox_ids"):
        return "One of the selected sending accounts will be used"
    if accounts_by_id:
        return "Sending account not chosen yet"
    return "A sending account will be chosen before sending"


def bundle_fingerprint(files: Dict[str, bytes]) -> str:
    digest = hashlib.sha256(f"assistant-import-v{IMPORT_VERSION}".encode())
    for filename in sorted(files, key=str.casefold):
        digest.update(filename.replace("\\", "/").casefold().encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(files[filename]).digest())
    return digest.hexdigest()


def canonical_variable_name(raw_name: str) -> str:
    compact = " ".join(str(raw_name or "").strip().replace("-", " ").replace("_", " ").split()).casefold()
    if compact in VARIABLE_ALIASES:
        return VARIABLE_ALIASES[compact]
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", compact)).strip("_")


def canonicalize_template_variables(text: str) -> str:
    """Accept friendly brace/name variants and store one renderer-safe syntax."""
    if not text:
        return text or ""

    def replacement(inner: str) -> str:
        name_part, separator, fallback = inner.partition("|")
        name = canonical_variable_name(name_part)
        if not name:
            return "{{" + inner + "}}"
        return "{{" + name + (("|" + fallback.strip()) if separator else "") + "}}"

    # Broad double braces are safe to normalize, including names containing spaces.
    text = re.sub(
        r"\{\{\s*([^{}]{1,120}?)\s*\}\}",
        lambda match: replacement(match.group(1)),
        text,
    )

    # Single braces are accepted only for variable-like words (not CSS/JSON blocks).
    text = re.sub(
        r"(?<!\{)\{\s*([A-Za-z][A-Za-z0-9 _-]{0,79}(?:\|[^{}]{0,80})?)\s*\}(?!\})",
        lambda match: replacement(match.group(1)),
        text,
    )

    # Friendly square-bracket names are limited to known semantic aliases so
    # ordinary prose such as [draft] is not rewritten unexpectedly.
    def bracket_replacer(match: re.Match) -> str:
        raw = match.group(1)
        normalized = " ".join(raw.casefold().split())
        return replacement(raw) if normalized in VARIABLE_ALIASES else match.group(0)

    return re.sub(r"\[\s*([A-Za-z][A-Za-z0-9 _-]{0,79})\s*\]", bracket_replacer, text)


def extract_template_variables(*parts: str) -> List[str]:
    variables = set()
    for part in parts:
        for match in TemplateEngine.VARIABLE_PATTERN.finditer(part or ""):
            variables.add(canonical_variable_name(match.group(1)))
    return sorted(variable for variable in variables if variable)


def variable_category(variable: str) -> str:
    normalized = canonical_variable_name(variable)
    if "linkedin" in normalized:
        return "linkedin"
    if "microsoft" in normalized or "esignature" in normalized:
        return "microsoft"
    return normalized


# ═════════════════════════════════════════════════════════════════════════
# Bundle classification
# ═════════════════════════════════════════════════════════════════════════

def classify_bundle(files: Dict[str, bytes], use_orphan_ai: bool = True) -> Dict[str, Any]:
    """Deterministic classification, then AI for leftovers.

    Two-pass design:
      Pass 1 — html/htm are always template bodies; non-text files pair with
              same-name (or prefix-matched) templates as attachments.
      Pass 2 — .txt files: same-name partner → attachment; else Subject: line
              → txt template; emails without subject → loose list; else AI.

    Partnerless pdfs/docx are sent to the AI (which can read their text) to
    decide: attach to the nearest template, or become standalone templates.

    Returns:
        {"accounts_file": name|None, "templates": [spec], "lists": [spec],
         "orphans": [names]}
    """
    templates: Dict[str, Dict[str, Any]] = {}
    attachments: Dict[str, List[Tuple[str, bytes]]] = {}
    lists: List[Dict[str, Any]] = []
    automation_rows: List[Dict[str, str]] = []
    accounts_file: Optional[str] = None
    orphans: List[str] = []
    deferred_txt: List[Tuple[str, str, str]] = []  # (name, base, stem)
    deferred_attach: List[Tuple[str, str, str]] = []  # attachments without a partner yet
    full_by_base: Dict[str, str] = {_safe_name(n): n for n in files}

    def _resolve(base: str) -> str:
        return full_by_base.get(base, base)

    def _match_template(stem: str) -> Optional[str]:
        """Exact stem match, or the closest template whose name is a prefix of
        the attachment's name (brochure-pack.pdf → brochure.html)."""
        if stem in templates:
            return stem
        normalized_stem = _identity(stem)
        for tstem in templates:
            if normalized_stem and normalized_stem == _identity(tstem):
                return tstem
        best: Optional[str] = None
        for tstem in templates:
            normalized_template = _identity(tstem)
            if (
                normalized_template
                and normalized_stem.startswith(normalized_template)
                and (best is None or len(normalized_template) > len(_identity(best)))
            ):
                best = tstem
        return best

    def _make_body_template(
        stem: str,
        base: str,
        text: str,
        ext: str,
        source_path: Optional[str] = None,
        storage_key: Optional[str] = None,
    ) -> None:
        raw_subject, raw_body = _parse_subject_and_body(text)
        subject = canonicalize_template_variables(raw_subject or stem.replace("-", " ").title())
        body = canonicalize_template_variables(raw_body or text)
        templates[storage_key or stem] = {
            "name": stem.replace("-", " ").replace("_", " ").strip().title(),
            "filename": base,
            "source_path": source_path or base,
            "identity": _identity(stem),
            "subject": subject,
            "body": body,
            "template_type": "plain_text" if ext == ".txt" else "html",
            "attachments": [],
        }

    # ── Pass 1 ──
    for name in sorted(files.keys()):
        base = _safe_name(name)
        ext = os.path.splitext(base)[1].lower()
        if ext not in ALLOWED_BUNDLE_EXTENSIONS:
            orphans.append(base)
            continue
        lower = base.lower()
        if lower.startswith("account") or "accounts" in lower.split(os.sep)[-1]:
            accounts_file = name
        elif "/lists/" in "/" + name.lower().replace("\\", "/"):
            # Anything inside a lists/ folder is a recipient list.
            if ext == ".csv":
                lists.append(_parse_list_csv(base, files[name]))
            elif ext == ".txt":
                lists.append(_parse_loose_list(base, files[name]))
            elif ext == ".pdf":
                text = _extract_text_from_bytes(base, files[name])
                lists.append(_parse_loose_list(base, text.encode("utf-8")) if text else {"name": _stem(base).title(), "rows": []})
            else:
                orphans.append(base)
        elif ext in {".html", ".htm"}:
            _make_body_template(_stem(base), base, _decode(files[name]), ".html", name)
        elif ext == ".txt":
            deferred_txt.append((name, base, _stem(base)))  # pass 2
        elif ext == ".csv":
            stem = _stem(base)
            if stem in {"automation", "mapping"}:
                automation_rows.extend(parse_automation_csv(files[name]))
                continue
            lists.append(_parse_list_csv(base, files[name]))
        elif ext in ATTACH_EXTENSIONS:
            target = _match_template(_stem(base))
            if target:
                attachments.setdefault(target, []).append((name, files[name]))
            else:
                # No partner YET — try again after all bodies are known.
                deferred_attach.append((name, base, _stem(base)))
        else:
            orphans.append(base)

    # ── Pass 2: .txt files ──
    for name, base, stem in deferred_txt:
        text = _decode(files[name])
        subject, _body = _parse_subject_and_body(text)
        target = _match_template(stem)
        if target and not subject:
            # Same-name txt beside an html template → it's an attachment.
            attachments.setdefault(target, []).append((name, files[name]))
            continue
        if subject:
            storage_key = stem if stem not in templates else f"duplicate:{stem}:{len(templates)}"
            _make_body_template(stem, base, text, ".txt", name, storage_key=storage_key)
        elif EMAIL_RE.search(text):
            lists.append(_parse_loose_list(base, files[name]))
        else:
            orphans.append(base)

    # ── Pass 2b: re-match attachments after *all* body templates exist ──
    # TXT bodies are intentionally deferred, so doing this before pass 2 made
    # exact TXT/PDF pairs look like unrelated standalone documents.
    for name, base, stem in deferred_attach:
        target = _match_template(stem)
        if target:
            attachments.setdefault(target, []).append((name, files[name]))
        else:
            orphans.append(base)

    for stem, atts in attachments.items():
        if stem in templates:
            templates[stem]["attachments"].extend(atts)

    # ── AI pass for leftovers (reads pdf/docx text to decide) ──
    if orphans and use_orphan_ai:
        try:
            decisions = _ai_classify_orphans(orphans, files)
            for filename, role in decisions.items():
                if filename not in orphans:
                    continue
                full = _resolve(filename)
                if role == "accounts":
                    accounts_file = full
                    orphans.remove(filename)
                elif role == "list":
                    lists.append(_parse_loose_list(filename, files[full]))
                    orphans.remove(filename)
                elif isinstance(role, dict) and role.get("attachment_for"):
                    target = _stem(str(role.get("attachment_for")))
                    if target in templates:
                        templates[target]["attachments"].append((full, files[full]))
                        orphans.remove(filename)
                elif role == "template_body":
                    stem = _stem(filename)
                    _make_body_template(stem, filename, _decode(files[full]), ".txt", full)
                    orphans.remove(filename)
                elif role == "standalone":
                    stem = _stem(filename)
                    text = _extract_text_from_bytes(filename, files[full]) or ""
                    templates[f"standalone:{stem}"] = {
                        "name": stem.replace("-", " ").title(),
                        "filename": full,
                        "source_path": full,
                        "identity": _identity(stem),
                        "subject": stem.replace("-", " ").title(),
                        "body": _standalone_pdf_wrapper(text),
                        "template_type": "html",
                        "attachments": [(full, files[full])],
                    }
                    orphans.remove(filename)
        except Exception as e:
            logger.warning(f"AI orphan classification failed: {e}")

    # Legacy direct imports still make undecided documents standalone here.
    # Staged imports defer this default until the whole-bundle AI has seen all
    # relationships and had an opportunity to attach or ignore the document.
    if use_orphan_ai:
        for name in list(orphans):
            ext = os.path.splitext(name)[1].lower()
            if ext in {".pdf", ".doc", ".docx"}:
                full = _resolve(name)
                stem = _stem(name)
                text = _extract_text_from_bytes(name, files[full]) or ""
                templates[f"standalone:{stem}"] = {
                    "name": stem.replace("-", " ").title(),
                    "filename": full,
                    "source_path": full,
                    "identity": _identity(stem),
                    "subject": stem.replace("-", " ").title(),
                    "body": _standalone_pdf_wrapper(text),
                    "template_type": "html",
                    "attachments": [(full, files[full])],
                }
                orphans.remove(name)

    return {
        "accounts_file": accounts_file,
        "templates": list(templates.values()),
        "lists": lists,
        "orphans": orphans,
        "automation_rows": automation_rows,
    }


def _ai_classify_orphans(orphans: List[str], files: Dict[str, bytes]) -> Dict[str, Any]:
    from services import llm_provider

    full_by_base = {_safe_name(n): n for n in files}
    inventory = []
    for name in orphans[:MAX_AI_FILES]:
        content = files.get(full_by_base.get(name, name), b"")
        ext = os.path.splitext(name)[1].lower()
        if ext in {".pdf", ".doc", ".docx"}:
            sample = (_extract_text_from_bytes(name, content) or "")[:400]
        else:
            sample = _decode(content[:300])[:200] if content else ""
        inventory.append({"filename": name, "ext": ext, "sample": sample})

    system = (
        "You classify leftover files from an email-campaign import bundle. "
        "Return ONLY JSON: {\"roles\": {\"<filename>\": <role>}} where role is one of: "
        "\"accounts\" (sender-account credentials), \"list\" (recipient emails), "
        "\"template_body\" (email template text), \"standalone\" (a document that should "
        "become its own email template with itself attached), "
        "{\"attachment_for\": \"<template stem>\"} (attachment belonging to an existing "
        "template — choose the closest template name), or \"ignore\"."
    )
    result = llm_provider.chat(
        system=system,
        messages=[{"role": "user", "content": "Classify these files:\n" + json.dumps(inventory)}],
        max_tokens=1000,
    )
    data = _extract_json_from_text(result.text)
    return (data or {}).get("roles", {}) if isinstance(data, dict) else {}


def _deduplicate_plan_resources(plan: Dict[str, Any]) -> List[str]:
    """Collapse same-identity bundle resources before IDs and mappings exist."""
    merged_lists: Dict[str, Dict[str, Any]] = {}
    for recipient_list in plan.get("lists") or []:
        identity = _identity(recipient_list.get("name") or recipient_list.get("source_path") or "")
        if not identity or identity not in merged_lists:
            merged_lists[identity or f"list-{len(merged_lists)}"] = recipient_list
            continue
        target = merged_lists[identity]
        rows_by_email = {
            str(row.get("email") or "").casefold(): row
            for row in target.get("rows") or []
            if row.get("email")
        }
        for row in recipient_list.get("rows") or []:
            email = str(row.get("email") or "").casefold()
            if not email:
                continue
            existing = rows_by_email.get(email)
            if existing is None:
                target.setdefault("rows", []).append(row)
                rows_by_email[email] = row
                continue
            if existing.get("name_inferred") and not row.get("name_inferred"):
                existing["first_name"] = row.get("first_name") or existing.get("first_name") or ""
                existing["last_name"] = row.get("last_name") or existing.get("last_name") or ""
                existing["name_inferred"] = False
            for field in ("first_name", "last_name", "company", "title"):
                if not existing.get(field) and row.get(field):
                    existing[field] = row[field]
    plan["lists"] = list(merged_lists.values())

    issues = []
    merged_templates: Dict[str, Dict[str, Any]] = {}
    for template in plan.get("templates") or []:
        identity = template.get("identity") or _identity(template.get("name") or template.get("source_path") or "")
        if not identity or identity not in merged_templates:
            merged_templates[identity or f"template-{len(merged_templates)}"] = template
            continue
        target = merged_templates[identity]
        if (
            target.get("subject") != template.get("subject")
            or target.get("body") != template.get("body")
            or target.get("template_type") != template.get("template_type")
        ):
            issues.append(
                f"Conflicting template files share the identity {target.get('name') or template.get('name')}"
            )
            continue
        attachment_digests = {
            _content_digest(content)
            for _filename, content in target.get("attachments") or []
        }
        for filename, content in template.get("attachments") or []:
            digest = _content_digest(content)
            if digest not in attachment_digests:
                target.setdefault("attachments", []).append((filename, content))
                attachment_digests.add(digest)
    plan["templates"] = list(merged_templates.values())
    return list(dict.fromkeys(issues))


# ═════════════════════════════════════════════════════════════════════════
# Accounts extraction (deterministic first, AI fallback)
# ═════════════════════════════════════════════════════════════════════════

def _universal_resend_domain() -> str:
    return (
        os.getenv("RESEND_VERIFIED_DOMAIN", "")
        or os.getenv("BREVO_VERIFIED_DOMAIN", "")
        or os.getenv("SES_VERIFIED_DOMAIN", "")
    ).strip().lower().lstrip("@")


def _normalize_resend_account(raw: Dict[str, Any]) -> Optional[Dict[str, str]]:
    email_value = str(
        raw.get("from_email") or raw.get("email") or raw.get("email_prefix")
        or raw.get("from_email_prefix") or raw.get("prefix") or ""
    ).strip().lower()
    prefix = email_value.split("@", 1)[0].strip()
    if not re.fullmatch(r"[a-z0-9._%+\-]+", prefix or ""):
        return None

    domain = _universal_resend_domain()
    if not domain:
        return None
    effective_email = f"{prefix}@{domain}"

    name = str(raw.get("name") or raw.get("account_name") or prefix).strip()[:255]
    from_name = str(raw.get("from_name") or raw.get("sender_name") or "").strip()[:255]
    if not name:
        return None
    return {
        "name": name,
        "email_prefix": prefix,
        "from_email": effective_email,
        "from_name": from_name,
    }


def _extract_accounts_deterministic(text: str) -> List[Dict[str, str]]:
    raw_accounts: List[Dict[str, Any]] = []

    # JSON account exports remain supported without involving the model.
    try:
        payload = json.loads(text)
        records = payload if isinstance(payload, list) else payload.get("accounts", []) if isinstance(payload, dict) else []
        raw_accounts.extend(record for record in records if isinstance(record, dict))
    except (TypeError, ValueError):
        pass

    # Header-based CSV/TSV account files.
    try:
        sample = text[:2048]
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        headers = [str(header or "").strip().casefold().replace(" ", "_") for header in (reader.fieldnames or [])]
        if {"name", "account_name"} & set(headers) and {
            "email", "from_email", "email_prefix", "from_email_prefix",
        } & set(headers):
            for row in reader:
                normalized = {
                    str(key or "").strip().casefold().replace(" ", "_"): value
                    for key, value in row.items()
                }
                raw_accounts.append(normalized)
    except Exception:
        pass

    # Human-readable rows used by bundle guides and Taker's accounts.txt:
    # 1. Name: Example, email: sender@uploaded-domain, from name: Jane Doe
    labelled_row = re.compile(
        r"(?:^|\n)\s*(?:\d+[.)]\s*)?"
        r"(?:account\s+)?name\s*:\s*(?P<name>[^,\n]+?)\s*,\s*"
        r"(?:from\s+)?email(?:\s+prefix)?\s*:\s*(?P<email>[^,\s]+)\s*,\s*"
        r"(?:from|sender)\s+name\s*:\s*(?P<from_name>[^\n]+)",
        re.IGNORECASE,
    )
    for match in labelled_row.finditer(text):
        raw_accounts.append(match.groupdict())

    # Multi-line or pipe/semicolon-delimited labelled blocks.
    blocks = re.split(r"(?m)(?=^\s*\d+[.)]\s*)|\n\s*\n+", text)
    for block in blocks:
        block = re.sub(r"^\s*\d+[.)]\s*", "", block)
        def labelled_value(label_pattern: str) -> str:
            match = re.search(
                rf"(?:^|[\n,;|])\s*{label_pattern}\s*[:=]\s*([^\n,;|]+)",
                block,
                re.IGNORECASE,
            )
            return match.group(1).strip() if match else ""

        name = labelled_value(r"(?:account\s+)?name")
        email = labelled_value(r"(?:from\s+)?email(?:\s+(?:address|prefix))?")
        from_name = labelled_value(r"(?:from|sender)\s+name")
        if name and email:
            raw_accounts.append({"name": name, "email": email, "from_name": from_name})

    out: List[Dict[str, str]] = []
    seen = set()
    for raw in raw_accounts:
        normalized = _normalize_resend_account(raw)
        if not normalized:
            continue
        key = normalized["from_email"].casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


def extract_accounts(filename: str, content: bytes, use_ai_fallback: bool = True) -> List[Dict[str, str]]:
    """Extract Resend accounts, accepting either a prefix or any full email.

    The uploaded domain is deliberately ignored whenever a universal verified
    domain is configured. AI is reserved for genuinely unstructured files.
    """
    from services import llm_provider

    text = _extract_text_from_bytes(filename, content)
    if not text and os.path.splitext(filename)[1].lower() in IMAGE_EXTENSIONS:
        text = llm_provider.extract_text_from_image(content)
    if not text:
        return []

    deterministic = _extract_accounts_deterministic(text)
    if deterministic:
        return deterministic
    if not use_ai_fallback:
        return []

    system = (
        "Extract Resend sending accounts from the provided text. Each account has a name, "
        "an email prefix (the part before @; a full email is also acceptable), and a "
        "sender/from name. Return ONLY JSON: {\"accounts\": [{\"name\": ..., "
        "\"email_prefix\": ..., \"from_name\": ...}]} . Do not invent missing accounts."
    )
    try:
        result = llm_provider.chat(
            system=system,
            messages=[{"role": "user", "content": text[:12000]}],
            max_tokens=3000,
        )
    except Exception as exc:
        logger.warning("AI account extraction failed for %s: %s", filename, exc)
        return []
    data = _extract_json_from_text(result.text)
    accounts = (data or {}).get("accounts", []) if isinstance(data, dict) else []
    out = []
    seen = set()
    for a in accounts:
        if not isinstance(a, dict):
            continue
        normalized = _normalize_resend_account(a)
        if not normalized or normalized["from_email"].casefold() in seen:
            continue
        seen.add(normalized["from_email"].casefold())
        out.append(normalized)
    return out


# ═════════════════════════════════════════════════════════════════════════
# List parsing
# ═════════════════════════════════════════════════════════════════════════

def _parse_list_csv(filename: str, content: bytes) -> Dict[str, Any]:
    text = _decode(content)
    rows: List[Dict[str, Any]] = []
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    for row in reader:
        normalized_row = {
            re.sub(r"[^a-z0-9]+", "_", str(key or "").strip().casefold()).strip("_"): str(value or "").strip()
            for key, value in row.items()
        }
        email = (
            normalized_row.get("email") or normalized_row.get("e_mail")
            or normalized_row.get("email_address") or normalized_row.get("mail") or ""
        ).strip().lower()
        if not EMAIL_RE.fullmatch(email):
            continue
        first_name = (normalized_row.get("first_name") or normalized_row.get("firstname") or "").strip()
        last_name = (normalized_row.get("last_name") or normalized_row.get("lastname") or normalized_row.get("surname") or "").strip()
        full_name = (
            normalized_row.get("full_name") or normalized_row.get("client_name")
            or normalized_row.get("recipient_name") or normalized_row.get("name") or ""
        ).strip()
        if full_name:
            name_parts = full_name.split()
            if not first_name:
                first_name = name_parts[0]
            if not last_name:
                last_name = " ".join(name_parts[1:])
        inferred_first, inferred_last = ("", "") if full_name else infer_recipient_name(email)
        name_inferred = bool((not first_name and inferred_first) or (not last_name and inferred_last))
        rows.append({
            "email": email,
            "first_name": first_name or inferred_first,
            "last_name": last_name or inferred_last,
            "company": (normalized_row.get("company") or normalized_row.get("company_name") or "").strip(),
            "title": (normalized_row.get("title") or normalized_row.get("job_title") or "").strip(),
            "name_inferred": name_inferred,
        })
    return {"name": _stem(filename).replace("-", " ").title(), "rows": rows}


def _parse_loose_list(filename: str, content: bytes) -> Dict[str, Any]:
    """Non-CSV list: one email per line (plus optional name)."""
    text = _decode(content)
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        email_match = EMAIL_RE.search(line)
        if not email_match:
            continue
        email = email_match.group(0).lower()
        name_before = line[:email_match.start()].strip(" ,;|")
        name_after = line[email_match.end():].strip(" ,;|")
        name_part = name_before or name_after
        inferred_first, inferred_last = infer_recipient_name(email)
        name_tokens = name_part.split()
        name_inferred = not name_tokens and bool(inferred_first or inferred_last)
        rows.append({
            "email": email,
            "first_name": (name_tokens[0] if name_tokens else "") or inferred_first,
            "last_name": (" ".join(name_tokens[1:]) if len(name_tokens) > 1 else "") or inferred_last,
            "company": "",
            "title": "",
            "name_inferred": name_inferred,
        })
    return {"name": _stem(filename).replace("-", " ").title(), "rows": rows}


# ═════════════════════════════════════════════════════════════════════════
# Execution
# ═════════════════════════════════════════════════════════════════════════

def _standalone_pdf_wrapper(extracted_text: str) -> str:
    preview = ""
    if extracted_text:
        preview = "<p>" + extracted_text[:800].replace("\n", "<br>") + "</p>"
    return (
        '<div style="font-family:Arial,sans-serif;padding:20px">'
        "<h2>See the attached document</h2>" + preview + "</div>"
    )


def _assistant_account_name(existing_accounts: List[SMTPAccount], base: str, prefix: str) -> str:
    existing_names = {str(item.name or "").casefold() for item in existing_accounts}
    clean_base = str(base or prefix).strip()[:255]
    if clean_base.casefold() not in existing_names:
        return clean_base
    candidate = f"{clean_base} — {prefix}"[:255]
    if candidate.casefold() not in existing_names:
        return candidate
    digest = hashlib.sha256(prefix.casefold().encode()).hexdigest()[:8]
    return f"{clean_base[:242]} — {digest}"


def _content_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _store_attachment(filename: str, content: bytes) -> Dict[str, Any]:
    return store_attachment(content, filename)


def _reusable_attachment(
    templates: List[SESEmailTemplate],
    filename: str,
    content: bytes,
) -> Optional[Dict[str, Any]]:
    wanted_name = _safe_name(filename).casefold()
    wanted_size = len(content)
    wanted_hash = _content_digest(content)
    for template in templates:
        for attachment in template.attachments or []:
            if _safe_name(str(attachment.get("filename") or "")).casefold() != wanted_name:
                continue
            if int(attachment.get("size") or -1) != wanted_size:
                continue
            try:
                filepath = resolve_attachment_path(attachment.get("stored_name", ""))
                with open(filepath, "rb") as stored:
                    if _content_digest(stored.read()) == wanted_hash:
                        return dict(attachment)
            except (OSError, ValueError):
                continue
    return None


def _choose_existing_by_identity(rows: List[Any], name: str) -> Optional[Any]:
    matches = [row for row in rows if _identity(getattr(row, "name", "")) == _identity(name)]
    if not matches:
        return None
    exact = [row for row in matches if str(getattr(row, "name", "")).casefold() == str(name).casefold()]
    return min(exact or matches, key=lambda row: row.id)


def _template_match_score(template: Dict[str, Any], recipient_list: Dict[str, Any]) -> int:
    list_name = recipient_list["source_name"]
    list_identity = _identity(list_name)
    source = str(template.get("source_name") or template.get("name") or "")
    haystack = " ".join((source, template.get("name") or "", template.get("subject") or "", template.get("body") or ""))
    haystack_identity = _identity(haystack)
    source_identity = _identity(source)
    if list_identity and list_identity in {source_identity, _identity(template.get("name") or "")}:
        return 120
    if list_identity and list_identity in haystack_identity:
        return 100

    stopwords = {"and", "company", "corporation", "group", "inc", "llc", "the", "technologies", "technology"}
    tokens = [
        token for token in re.findall(r"[a-z0-9]+", list_name.casefold())
        if token not in stopwords and (len(token) >= 3 or token in {"jp"})
    ]
    source_tokens = set(re.findall(r"[a-z0-9]+", source.casefold()))
    body_tokens = set(re.findall(r"[a-z0-9]+", haystack.casefold()))
    if tokens and all(token in body_tokens for token in tokens):
        return 90
    overlap = sum(1 for token in tokens if token in source_tokens)
    if overlap:
        return 60 + overlap * 5
    if tokens and any(token in body_tokens for token in tokens):
        return 50
    return 0


def _ai_bundle_hints(
    account_records: List[Dict[str, Any]],
    list_records: List[Dict[str, Any]],
    template_records: List[Dict[str, Any]],
    file_inventory: Optional[List[Dict[str, Any]]] = None,
    existing_resources: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Ask DeepSeek for relationships/semantic tags, never for mutations."""
    from services import llm_provider

    if not llm_provider.is_configured():
        return {}
    payload = {
        "universal_sender_domain": _universal_resend_domain(),
        "files": file_inventory or [],
        "accounts": [
            {"id": row["id"], "name": row["source_name"], "from_name": row.get("from_name", "")}
            for row in account_records
        ],
        "lists": [
            {
                "id": row["id"],
                "name": row["source_name"],
                "recipient_count": row.get("recipient_count", 0),
                "recipient_domains": row.get("recipient_domains", []),
                "sample_rows": row.get("sample_rows", []),
            }
            for row in list_records
        ],
        "templates": [
            {
                "id": row["id"],
                "filename": row["source_name"],
                "subject": row.get("subject", ""),
                "body": _compact_text_sample(row.get("body") or ""),
                "attachments": row.get("attachment_names", []),
                "variables": row.get("variables", []),
            }
            for row in template_records
        ],
        "existing_resources": existing_resources or {},
    }
    system = (
        "Analyze one complete email-campaign bundle and its user's existing resources. Infer file "
        "roles and semantic relationships, including "
        "a template's recipient list and sending account from filenames, company names, body, "
        "signature, and recipient domains. Understand that a template can use a PDF, a "
        "user-supplied link, both, or neither. The supplied sender domain is universal; uploaded "
        "domains are not identities. Emit a mapping only when the relationship is clear; put any "
        "ambiguity in unclear instead of guessing or using upload order. If the deterministic "
        "accounts inventory is empty but an account file sample exists, extract its real rows without "
        "inventing any. Never invent variable values "
        "or links. Treat every supplied link string as opaque: do not open, validate, rewrite, or "
        "normalize it. Return ONLY JSON with: "
        "{\"file_roles\":[{\"filename\":str,\"role\":\"accounts|recipient_list|template|attachment|standalone|instructions|ignore\",\"template_id\":int}],"
        "\"mappings\":[{\"template_id\":int,\"list_id\":int,\"account_id\":int}],"
        "\"extracted_accounts\":[{\"name\":str,\"email_prefix\":str,\"from_name\":str}],"
        "\"variable_categories\":[{\"template_id\":int,\"variable\":str,\"category\":str}],"
        "\"asset_modes\":[{\"template_id\":int,\"mode\":\"attachment|link|mixed|plain\"}],"
        "\"unclear\":[str]}."
    )
    try:
        result = llm_provider.chat(
            system=system,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            max_tokens=4000,
        )
        data = _extract_json_from_text(result.text)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Whole-bundle AI analysis failed; deterministic plan retained: %s", exc)
        return {}


def _apply_ai_file_roles(
    plan: Dict[str, Any],
    files: Dict[str, bytes],
    ai_hints: Dict[str, Any],
) -> bool:
    """Apply constrained AI roles only to files deterministic parsing left orphaned."""
    orphan_labels = {str(name) for name in plan.get("orphans") or []}
    if not orphan_labels:
        return False
    file_names = {name.casefold(): name for name in files}
    basename_candidates: Dict[str, List[str]] = {}
    for name in files:
        basename_candidates.setdefault(_safe_name(name).casefold(), []).append(name)
    role_rows = [row for row in ai_hints.get("file_roles") or [] if isinstance(row, dict)]
    changed = False
    processed_files = set()
    processed_labels = set()

    def resolve_filename(row: Dict[str, Any]) -> str:
        requested = str(row.get("filename") or row.get("path") or "").strip().casefold()
        exact = file_names.get(requested)
        if exact:
            return exact
        basename_matches = basename_candidates.get(_safe_name(requested).casefold(), [])
        return basename_matches[0] if len(basename_matches) == 1 else ""

    def orphan_label(filename: str) -> str:
        if filename in orphan_labels:
            return filename
        basename = _safe_name(filename)
        return basename if basename in orphan_labels else ""

    # Register structural resources before attachments so a later role can
    # safely target a newly recognized template.
    for row in role_rows:
        filename = resolve_filename(row)
        label = orphan_label(filename)
        if not filename or not label:
            continue
        role = re.sub(r"[^a-z]+", "_", str(row.get("role") or "").casefold()).strip("_")
        extension = os.path.splitext(filename)[1].casefold()
        if role in {"instructions", "instruction", "ignore", "ignored", "supporting_document"}:
            processed_files.add(filename)
            processed_labels.add(label)
            changed = True
        elif role in {"accounts", "account", "sending_accounts"} and not plan.get("accounts_file"):
            if extension in {".txt", ".csv", ".json"}:
                plan["accounts_file"] = filename
                processed_files.add(filename)
                processed_labels.add(label)
                changed = True
        elif role in {"recipient_list", "recipient_lists", "list", "recipients"}:
            if extension in {".csv", ".txt"}:
                parsed = _parse_list_csv(filename, files[filename]) if extension == ".csv" else _parse_loose_list(filename, files[filename])
                if parsed.get("rows"):
                    parsed["source_path"] = filename
                    plan["lists"].append(parsed)
                    processed_files.add(filename)
                    processed_labels.add(label)
                    changed = True
        elif role in {"template", "email_template", "message"} and extension in {".txt", ".md", ".html", ".htm"}:
            base = _safe_name(filename)
            stem = _stem(base)
            raw_text = _decode(files[filename])
            raw_subject, raw_body = _parse_subject_and_body(raw_text)
            parsed = {
                "name": stem.replace("-", " ").replace("_", " ").strip().title(),
                "filename": base,
                "source_path": filename,
                "identity": _identity(stem),
                "subject": canonicalize_template_variables(raw_subject or stem.replace("-", " ").title()),
                "body": canonicalize_template_variables(raw_body or raw_text),
                "template_type": "html" if extension in {".html", ".htm"} else "plain_text",
                "attachments": [],
            }
            requested_mode = str(row.get("mode") or row.get("asset_mode") or "").casefold()
            if requested_mode in {"attachment", "link", "mixed", "plain"}:
                parsed["asset_mode"] = requested_mode
            plan["templates"].append(parsed)
            processed_files.add(filename)
            processed_labels.add(label)
            changed = True
        elif role in {"standalone", "standalone_template", "document_template"} and extension in {".pdf", ".doc", ".docx"}:
            base = _safe_name(filename)
            stem = _stem(base)
            text = _extract_text_from_bytes(base, files[filename]) or ""
            plan["templates"].append({
                "name": stem.replace("-", " ").replace("_", " ").title(),
                "filename": base,
                "source_path": filename,
                "identity": _identity(stem),
                "subject": stem.replace("-", " ").replace("_", " ").title(),
                "body": _standalone_pdf_wrapper(text),
                "template_type": "html",
                "attachments": [(filename, files[filename])],
                "asset_mode": "attachment",
            })
            processed_files.add(filename)
            processed_labels.add(label)
            changed = True

    for row in role_rows:
        filename = resolve_filename(row)
        label = orphan_label(filename)
        if not filename or not label or filename in processed_files:
            continue
        role = re.sub(r"[^a-z]+", "_", str(row.get("role") or "").casefold()).strip("_")
        if role not in {"attachment", "template_attachment", "asset"}:
            continue
        target = None
        try:
            target_id = int(row.get("template_id") or row.get("target_template_id") or 0)
        except (TypeError, ValueError):
            target_id = 0
        if 1 <= target_id <= len(plan["templates"]):
            target = plan["templates"][target_id - 1]
        if target is None:
            target_name = _identity(str(row.get("template") or row.get("target_template") or ""))
            attachment_stem = _identity(_stem(filename))
            candidates = [
                template for template in plan["templates"]
                if target_name in {
                    _identity(template.get("name") or ""),
                    _identity(template.get("filename") or ""),
                    _identity(template.get("source_path") or ""),
                }
            ] if target_name else []
            if not candidates:
                candidates = []
                for template in plan["templates"]:
                    template_identity = _identity(template.get("name") or "")
                    if template_identity and attachment_stem.startswith(template_identity):
                        candidates.append(template)
            target = candidates[0] if len(candidates) == 1 else None
        if target is None or len(files[filename]) > MAX_FILE_BYTES:
            continue
        existing_digests = {_content_digest(content) for _name, content in target.get("attachments") or []}
        if _content_digest(files[filename]) not in existing_digests:
            target.setdefault("attachments", []).append((filename, files[filename]))
        processed_files.add(filename)
        processed_labels.add(label)
        changed = True

    if processed_labels:
        plan["orphans"] = [name for name in plan.get("orphans") or [] if name not in processed_labels]
    return changed


def _promote_document_orphans(plan: Dict[str, Any], files: Dict[str, bytes]) -> bool:
    """Retain undecided document assets as standalone reviewed templates."""
    by_basename: Dict[str, List[str]] = {}
    for filename in files:
        by_basename.setdefault(_safe_name(filename), []).append(filename)
    promoted = []
    for orphan in plan.get("orphans") or []:
        if os.path.splitext(orphan)[1].casefold() not in {".pdf", ".doc", ".docx"}:
            continue
        candidates = [orphan] if orphan in files else by_basename.get(_safe_name(orphan), [])
        if len(candidates) != 1:
            continue
        filename = candidates[0]
        base = _safe_name(filename)
        stem = _stem(base)
        text = _extract_text_from_bytes(base, files[filename]) or ""
        plan["templates"].append({
            "name": stem.replace("-", " ").replace("_", " ").title(),
            "filename": base,
            "source_path": filename,
            "identity": _identity(stem),
            "subject": stem.replace("-", " ").replace("_", " ").title(),
            "body": _standalone_pdf_wrapper(text),
            "template_type": "html",
            "attachments": [(filename, files[filename])],
            "asset_mode": "attachment",
        })
        promoted.append(orphan)
    if promoted:
        promoted_set = set(promoted)
        plan["orphans"] = [name for name in plan.get("orphans") or [] if name not in promoted_set]
    return bool(promoted)


def _logical_stage_records(
    accounts: List[Dict[str, Any]],
    plan: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], int]:
    account_records = [
        {
            "id": index,
            "source_name": account["name"],
            "name": account["name"],
            "from_name": account.get("from_name") or "",
            "email_prefix": account["email_prefix"],
            "from_email": account["from_email"],
            "inbox_id": index,
        }
        for index, account in enumerate(accounts, 1)
    ]
    list_records = []
    inferred_name_count = 0
    for index, recipient_list in enumerate(plan["lists"], 1):
        emails = sorted({row["email"].casefold() for row in recipient_list.get("rows") or []})
        list_records.append({
            "id": index,
            "name": recipient_list["name"],
            "source_name": recipient_list["name"],
            "source_path": recipient_list.get("source_path") or recipient_list["name"],
            "recipient_count": len(emails),
            "recipient_fingerprint": hashlib.sha256("\n".join(emails).encode()).hexdigest(),
            "recipient_domains": sorted({email.rsplit("@", 1)[-1] for email in emails})[:20],
            "sample_rows": (recipient_list.get("rows") or [])[:5],
            "rows": recipient_list.get("rows") or [],
        })
        inferred_name_count += sum(1 for row in recipient_list.get("rows") or [] if row.get("name_inferred"))
    template_records = []
    for index, template in enumerate(plan["templates"], 1):
        variables = extract_template_variables(template["subject"], template["body"])
        template_records.append({
            "id": index,
            "name": template["name"],
            "source_name": template.get("filename") or template["name"],
            "source_path": template.get("source_path") or template.get("filename") or template["name"],
            "identity": template.get("identity") or _identity(template["name"]),
            "subject": template["subject"],
            "body": template["body"],
            "template_type": template["template_type"],
            "variables": variables,
            "attachment_names": [name for name, _content in template.get("attachments", [])],
            "asset_mode": template.get("asset_mode"),
        })
    return account_records, list_records, template_records, inferred_name_count


def _build_smart_mappings(
    account_records: List[Dict[str, Any]],
    list_records: List[Dict[str, Any]],
    template_records: List[Dict[str, Any]],
    ai_hints: Optional[Dict[str, Any]] = None,
    automation_rows: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, Any]]:
    """Build one validated list/template/account mapping without order-based shifts."""
    ai_hints = ai_hints or {}
    accounts_by_id = {row["id"]: row for row in account_records}
    lists_by_id = {row["id"]: row for row in list_records}
    templates_by_id = {row["id"]: row for row in template_records}
    hinted_by_list: Dict[int, Dict[str, Any]] = {}

    # Explicit automation.csv relationships always outrank inferred ones.
    for automation in automation_rows or []:
        template = next((
            row for row in template_records
            if _identity(row["source_name"]) == _identity(automation.get("template", ""))
            or _identity(row["name"]) == _identity(automation.get("template", ""))
        ), None)
        recipient_list = next((
            row for row in list_records
            if _identity(row["source_name"]) == _identity(automation.get("list", ""))
            or _identity(row["name"]) == _identity(automation.get("list", ""))
        ), None)
        if not template or not recipient_list:
            continue
        inbox_spec = str(automation.get("inboxes") or "").strip()
        selected_accounts = list(account_records)
        if inbox_spec and inbox_spec.casefold() != "warmed":
            wanted = {_identity(part) for part in inbox_spec.split(",") if part.strip()}
            selected_accounts = [
                account for account in account_records
                if _identity(account["source_name"]) in wanted
                or _identity(account.get("email_prefix", "")) in wanted
            ]
        hinted_by_list[recipient_list["id"]] = {
            "template_id": template["id"],
            "account_id": selected_accounts[0]["id"] if len(selected_accounts) == 1 else None,
            "inbox_ids": [account["inbox_id"] for account in selected_accounts if account.get("inbox_id")],
            "send_timezone": automation.get("send_timezone") or "US/Eastern",
            "start": bool(automation.get("start")),
            "source": "automation",
        }

    for hint in ai_hints.get("mappings") or []:
        if not isinstance(hint, dict):
            continue
        try:
            template_id = int(hint.get("template_id"))
            list_id = int(hint.get("list_id"))
            account_id = int(hint.get("account_id")) if hint.get("account_id") is not None else None
        except (TypeError, ValueError):
            continue
        if template_id not in templates_by_id or list_id not in lists_by_id:
            continue
        if account_id is not None and account_id not in accounts_by_id:
            account_id = None
        if list_id in hinted_by_list:
            continue
        hinted_by_list[list_id] = {
            "template_id": template_id,
            "account_id": account_id,
            "inbox_ids": [],
            "send_timezone": "US/Eastern",
            "start": False,
            "source": "ai",
        }

    used_templates = set()
    mappings: List[Dict[str, Any]] = []
    for recipient_list in list_records:
        hint = hinted_by_list.get(recipient_list["id"])
        template = templates_by_id.get(hint["template_id"]) if hint else None
        source = hint.get("source", "ai") if template and hint else "deterministic"

        if template is None:
            candidates = sorted(
                (
                    (_template_match_score(candidate, recipient_list), candidate)
                    for candidate in template_records
                ),
                key=lambda item: (-item[0], item[1]["id"] in used_templates, item[1]["id"]),
            )
            if candidates:
                template = candidates[0][1]
                source = "deterministic" if candidates[0][0] > 0 else "fallback"
        if template is None:
            continue
        used_templates.add(template["id"])

        account = accounts_by_id.get(hint.get("account_id")) if hint else None
        if account is None:
            template_haystack = _identity(" ".join((
                template["source_name"], template.get("subject", ""), template.get("body", ""),
            )))
            scored_accounts = []
            for candidate in account_records:
                markers = (
                    _relationship_name_markers(candidate.get("source_name", ""))
                    | _relationship_name_markers(candidate.get("name", ""))
                    | {_identity(candidate.get("from_name", ""))}
                )
                score = max(
                    (len(marker) for marker in markers if marker and marker in template_haystack),
                    default=0,
                )
                scored_accounts.append((score, candidate))
            best_score = max((score for score, _candidate in scored_accounts), default=0)
            best_accounts = [
                candidate for score, candidate in scored_accounts
                if best_score > 0 and score == best_score
            ]
            account = best_accounts[0] if len(best_accounts) == 1 else None
        if account is None and len(account_records) == 1:
            account = account_records[0]

        inbox_ids = list(hint.get("inbox_ids") or []) if hint else []
        if not inbox_ids and account and account.get("inbox_id"):
            inbox_ids = [account["inbox_id"]]

        mappings.append({
            "template_id": template["id"],
            "list_id": recipient_list["id"],
            "account_id": account["id"] if account else None,
            "inbox_ids": inbox_ids,
            "send_timezone": hint.get("send_timezone", "US/Eastern") if hint else "US/Eastern",
            "start": bool(hint.get("start")) if hint else False,
            "source": source,
        })
    return mappings


def _build_variable_state(
    template_records: List[Dict[str, Any]],
    ai_hints: Optional[Dict[str, Any]] = None,
    previous_context: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, str]]]:
    ai_hints = ai_hints or {}
    previous_context = previous_context or {}
    previous_values = previous_context.get("template_values") or {}
    previously_skipped = {
        (
            int(item.get("template_id") or 0),
            canonical_variable_name(item.get("variable") or ""),
        )
        for item in previous_context.get("requirements") or []
        if item.get("skipped")
    }
    category_hints: Dict[Tuple[int, str], str] = {}
    valid_template_ids = {row["id"] for row in template_records}
    for hint in ai_hints.get("variable_categories") or []:
        if not isinstance(hint, dict):
            continue
        try:
            template_id = int(hint.get("template_id"))
        except (TypeError, ValueError):
            continue
        variable = canonical_variable_name(hint.get("variable") or "")
        category = canonical_variable_name(hint.get("category") or "")
        if template_id in valid_template_ids and variable and category:
            category_hints[(template_id, variable)] = category

    requirements: List[Dict[str, Any]] = []
    template_values: Dict[str, Dict[str, str]] = {}
    for template in template_records:
        template_id = template["id"]
        value_map = dict(previous_values.get(str(template_id)) or {})
        attachment_names = template.get("attachment_names") or []
        if "pdf_filename" in template.get("variables", []) and attachment_names:
            pdf_names = [name for name in attachment_names if os.path.splitext(name)[1].casefold() == ".pdf"]
            value_map["pdf_filename"] = _safe_name((pdf_names or attachment_names)[0])
        template_values[str(template_id)] = value_map

        combined = "\n".join((template.get("subject") or "", template.get("body") or ""))
        matches_by_variable: Dict[str, List[re.Match]] = {}
        for match in TemplateEngine.VARIABLE_PATTERN.finditer(combined):
            variable = canonical_variable_name(match.group(1))
            matches_by_variable.setdefault(variable, []).append(match)
        for variable, matches in sorted(matches_by_variable.items()):
            if variable in AUTOMATIC_VARIABLES:
                continue
            # A fallback resolves the requirement only when every occurrence
            # of this variable provides one.
            if matches and all(match.group(2) is not None for match in matches):
                continue
            current_value = str(value_map.get(variable) or "")
            was_skipped = not current_value and (template_id, variable) in previously_skipped
            requirements.append({
                "id": f"{template_id}:{variable}",
                "template_id": template_id,
                "template": template["name"],
                "variable": variable,
                "category": category_hints.get((template_id, variable), variable_category(variable)),
                "occurrences": len(matches),
                "value": current_value,
                "resolved": bool(current_value) or was_skipped,
                "skipped": was_skipped,
            })
    return requirements, template_values


def variable_groups(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for requirement in context.get("requirements") or []:
        category = canonical_variable_name(requirement.get("category") or requirement.get("variable") or "value")
        group = grouped.setdefault(category, {
            "category": category,
            "variables": set(),
            "templates": set(),
            "count": 0,
            "resolved_count": 0,
            "skipped_count": 0,
        })
        group["variables"].add(requirement.get("variable") or category)
        group["templates"].add(requirement.get("template") or "Template")
        group["count"] += 1
        if requirement.get("resolved"):
            group["resolved_count"] += 1
        if requirement.get("skipped"):
            group["skipped_count"] += 1
    return [
        {
            **group,
            "variables": sorted(group["variables"]),
            "templates": sorted(group["templates"]),
            "resolved": group["resolved_count"] == group["count"],
        }
        for _category, group in sorted(grouped.items())
    ]


def variable_template_previews(
    context: Dict[str, Any],
    category: str,
    template_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return staged template metadata for a variable category.

    Listing a category intentionally omits template bodies. A full body is
    returned only when a specific logical template ID is requested, keeping
    assistant review payloads and preview-list responses small.
    """
    normalized_category = canonical_variable_name(category)
    matching_requirements: Dict[int, List[Dict[str, Any]]] = {}
    for requirement in context.get("requirements") or []:
        requirement_category = canonical_variable_name(
            requirement.get("category") or requirement.get("variable") or "value"
        )
        if requirement_category != normalized_category:
            continue
        try:
            requirement_template_id = int(requirement.get("template_id"))
        except (TypeError, ValueError):
            continue
        matching_requirements.setdefault(requirement_template_id, []).append(requirement)

    requested_template_id: Optional[int] = None
    if template_id is not None:
        try:
            requested_template_id = int(template_id)
        except (TypeError, ValueError):
            return []
        if requested_template_id not in matching_requirements:
            return []

    previews: List[Dict[str, Any]] = []
    templates = (context.get("manifest") or {}).get("templates") or []
    for template in templates:
        try:
            logical_id = int(template.get("id"))
        except (TypeError, ValueError):
            continue
        requirements = matching_requirements.get(logical_id)
        if not requirements or (requested_template_id is not None and logical_id != requested_template_id):
            continue
        pending_variables = sorted({
            str(item.get("variable") or normalized_category)
            for item in requirements
            if not item.get("resolved")
        })
        skipped_variables = sorted({
            str(item.get("variable") or normalized_category)
            for item in requirements
            if item.get("skipped")
        })
        preview = {
            "id": logical_id,
            "name": str(template.get("name") or "Template"),
            "subject": str(template.get("subject") or ""),
            "template_type": str(template.get("template_type") or "html"),
            "variables": sorted({
                str(item.get("variable") or normalized_category)
                for item in requirements
            }),
            "pending_variables": pending_variables,
            "skipped_variables": skipped_variables,
            "resolved": not pending_variables,
            "attachment_names": [
                _safe_name(str(name)) for name in template.get("attachment_names") or []
            ],
        }
        if requested_template_id is not None:
            preview["body"] = str(template.get("body") or "")
        previews.append(preview)

    return sorted(previews, key=lambda item: (item["resolved"], item["name"].casefold(), item["id"]))


def unresolved_requirements(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [item for item in context.get("requirements") or [] if not item.get("resolved")]


def _assignment_scope(requirements: List[Dict[str, Any]], label: str) -> Tuple[str, List[int]]:
    normalized_label = canonical_variable_name(label)
    category = variable_category(normalized_label)
    matching_categories = {
        canonical_variable_name(item.get("category") or item.get("variable") or "")
        for item in requirements
    }
    if category not in matching_categories:
        exact_variable = next((
            canonical_variable_name(item.get("variable") or "") for item in requirements
            if canonical_variable_name(item.get("variable") or "") == normalized_label
        ), "")
        category = exact_variable or category

    label_identity = _identity(label)
    scoped_templates = {
        int(item["template_id"]) for item in requirements
        if _identity(item.get("template") or "")
        and _identity(item.get("template") or "") in label_identity
    }
    return category, sorted(scoped_templates)


def _apply_variable_assignment(
    context: Dict[str, Any],
    label: str,
    value: str,
    template_ids: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    requirements = context.get("requirements") or []
    category, inferred_template_ids = _assignment_scope(requirements, label)
    scope_is_explicit = template_ids is not None or bool(inferred_template_ids)
    wanted_templates = set(template_ids if template_ids is not None else inferred_template_ids)
    normalized_label = canonical_variable_name(label)
    applied = []
    for requirement in requirements:
        requirement_category = canonical_variable_name(requirement.get("category") or requirement.get("variable") or "")
        requirement_variable = canonical_variable_name(requirement.get("variable") or "")
        if requirement_category != category and requirement_variable != normalized_label:
            continue
        template_id = int(requirement["template_id"])
        if scope_is_explicit and template_id not in wanted_templates:
            continue
        clean_value = str(value).strip()
        if not clean_value:
            continue
        context.setdefault("template_values", {}).setdefault(str(template_id), {})[requirement_variable] = clean_value
        requirement["value"] = clean_value
        requirement["resolved"] = True
        requirement["skipped"] = False
        applied.append(requirement)
    return applied


def _skip_variable_requirements(
    context: Dict[str, Any],
    categories: Optional[List[str]] = None,
    template_ids: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """Mark explicitly omitted custom values as intentionally blank.

    Skipping is reversible: a later explicit assignment replaces the blank
    choice through ``_apply_variable_assignment``. Only currently unresolved
    requirements are changed.
    """
    wanted_categories = {
        canonical_variable_name(category)
        for category in categories or []
        if canonical_variable_name(category)
    }
    wanted_templates = {int(template_id) for template_id in template_ids or []}
    skipped: List[Dict[str, Any]] = []
    for requirement in unresolved_requirements(context):
        category = canonical_variable_name(
            requirement.get("category") or requirement.get("variable") or ""
        )
        template_id = int(requirement.get("template_id") or 0)
        if wanted_categories and category not in wanted_categories:
            continue
        if wanted_templates and template_id not in wanted_templates:
            continue
        variable = canonical_variable_name(requirement.get("variable") or "")
        context.setdefault("template_values", {}).setdefault(str(template_id), {}).pop(variable, None)
        requirement["value"] = ""
        requirement["resolved"] = True
        requirement["skipped"] = True
        skipped.append(requirement)
    if skipped:
        context["pending_assignment"] = None
    return skipped


def _mentioned_variable_categories(requirements: List[Dict[str, Any]], message: str) -> List[str]:
    message_identity = _identity(message)
    mentioned = set()
    for requirement in requirements:
        category = canonical_variable_name(
            requirement.get("category") or requirement.get("variable") or ""
        )
        candidates = {
            category,
            str(requirement.get("variable") or ""),
            category.replace("_", " "),
            str(requirement.get("variable") or "").replace("_", " "),
        }
        if any(
            len(_identity(candidate)) >= 3 and _identity(candidate) in message_identity
            for candidate in candidates
        ):
            mentioned.add(category)
    return sorted(mentioned)


def _has_prepare_intent(message: str) -> bool:
    return bool(re.search(
        r"\b(create|prepare|build|continue|proceed|move\s+on|go\s+ahead|carry\s+on|finish)\b",
        message,
        re.IGNORECASE,
    ))


def _explicit_skip_intent(
    context: Dict[str, Any],
    message: str,
) -> Optional[Dict[str, Any]]:
    """Resolve direct requests to continue while leaving custom values blank."""
    pending = unresolved_requirements(context)
    if not pending:
        return None
    compact = " ".join(str(message or "").strip().split())
    if not compact:
        return None
    categories = _mentioned_variable_categories(pending, compact)

    skip_signal = bool(
        re.search(
            r"\b(?:without|skip|omit|ignore)\b[^?]*\b(?:value|values|variable|variables|field|fields|"
            r"detail|details|them|those|these|rest|remaining)\b",
            compact,
            re.IGNORECASE,
        )
        or re.search(
            r"\b(?:leave|keep)\b[^?]*\b(?:blank|empty|unset|unfilled)\b",
            compact,
            re.IGNORECASE,
        )
        or re.search(
            r"\b(?:skip|omit|ignore)\b\s+(?:the\s+)?(?:rest|remaining|missing|custom\s+values?)\b",
            compact,
            re.IGNORECASE,
        )
        or (
            _has_prepare_intent(compact)
            and bool(re.search(r"\b(?:anyway|as[- ]is|with\s+blanks?)\b", compact, re.IGNORECASE))
        )
        or (
            bool(categories)
            and bool(re.search(r"\b(?:without|skip|omit|ignore)\b", compact, re.IGNORECASE))
        )
    )
    if not skip_signal:
        return None

    is_question = bool(re.match(
        r"^(?:can|could|should|would|will|do)\s+i\b|^(?:what|how|why|is|are)\b",
        compact,
        re.IGNORECASE,
    ))
    is_polite_command = bool(re.match(
        r"^(?:can|could|would|will)\s+you\b|^please\b",
        compact,
        re.IGNORECASE,
    ))
    template_ids = _mentioned_template_ids(pending, compact)
    if is_question and not is_polite_command:
        return {
            "intent": "confirm_skip_values",
            "skipped": [],
            "remaining": pending,
            "groups": variable_groups(context),
            "categories": categories,
            "template_ids": template_ids,
            "proceed": False,
        }

    skipped = _skip_variable_requirements(
        context,
        categories=categories or None,
        template_ids=template_ids or None,
    )
    if not skipped:
        return None
    return {
        "intent": "skip_values",
        "skipped": skipped,
        "remaining": unresolved_requirements(context),
        "groups": variable_groups(context),
        "categories": sorted({
            canonical_variable_name(item.get("category") or item.get("variable") or "")
            for item in skipped
        }),
        "template_ids": sorted({int(item["template_id"]) for item in skipped}),
        "proceed": _has_prepare_intent(compact),
    }


def _matching_variable_requirements(requirements: List[Dict[str, Any]], label: str) -> List[Dict[str, Any]]:
    category, _template_ids = _assignment_scope(requirements, label)
    normalized_label = canonical_variable_name(label)
    return [
        requirement for requirement in requirements
        if canonical_variable_name(requirement.get("category") or requirement.get("variable") or "") == category
        or canonical_variable_name(requirement.get("variable") or "") == normalized_label
    ]


def _mentioned_template_ids(requirements: List[Dict[str, Any]], message: str) -> List[int]:
    message_identity = _identity(message)
    return sorted({
        int(requirement["template_id"])
        for requirement in requirements
        if len(_identity(requirement.get("template") or "")) >= 3
        and _identity(requirement.get("template") or "") in message_identity
    })


def _has_global_scope(message: str) -> bool:
    return bool(re.search(
        r"\b(all|both|each|every|everywhere|remaining|across|these|those|same)\b",
        message,
        re.IGNORECASE,
    ))


def _is_affirmative_scope_reply(message: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(message or "").casefold()).strip()
    return bool(re.fullmatch(
        r"(?:yes|yeah|yep|sure|ok|okay|correct|right)(?: please)?"
        r"|(?:yes|yeah|yep|sure|ok|okay) (?:use|apply|set) (?:it|that|this|the value)(?: please)?"
        r"|(?:use|apply|set) (?:it|that|this|the value)(?: please)?"
        r"|do it|go ahead",
        normalized,
    ))


def _is_declined_scope_reply(message: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(message or "").casefold()).strip()
    return bool(
        re.fullmatch(r"no|nope|not all|do not|don t", normalized)
        or re.search(r"\b(individual|individually|separate|separately|different)\b", normalized)
    )


def resolve_mapping_message(context: Dict[str, Any], message: str) -> Optional[Dict[str, Any]]:
    """Apply an explicit conversational mapping correction to a staged plan."""
    if context.get("materialized") or not context.get("relationship_issues"):
        return None
    if not re.search(r"\b(use|map|pair|assign|connect|belongs|with|for|to)\b", message, re.IGNORECASE):
        return None
    manifest = context.get("manifest") or {}
    templates = manifest.get("templates") or []
    lists = manifest.get("lists") or []
    accounts = manifest.get("accounts") or []
    message_identity = _identity(message)

    def mentioned(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        matches = []
        for record in records:
            identities = {
                _identity(record.get("name") or ""),
                _identity(record.get("source_name") or ""),
            }
            if any(identity and len(identity) >= 2 and identity in message_identity for identity in identities):
                matches.append(record)
        return matches

    mentioned_templates = mentioned(templates)
    mentioned_lists = mentioned(lists)
    mentioned_accounts = mentioned(accounts)
    if len(mentioned_templates) != 1 or len(mentioned_lists) != 1 or len(mentioned_accounts) > 1:
        return None

    template = mentioned_templates[0]
    recipient_list = mentioned_lists[0]
    mappings = list(context.get("mappings") or [])
    existing = next((
        row for row in mappings
        if int(row.get("list_id") or 0) == int(recipient_list["id"])
    ), None)
    account = mentioned_accounts[0] if mentioned_accounts else None
    if account is None and existing and existing.get("account_id") is not None:
        account = next((
            row for row in accounts
            if int(row["id"]) == int(existing["account_id"])
        ), None)
    if account is None and len(accounts) == 1:
        account = accounts[0]

    corrected = {
        "template_id": int(template["id"]),
        "list_id": int(recipient_list["id"]),
        "account_id": int(account["id"]) if account else None,
        "inbox_ids": [int(account.get("inbox_id") or account["id"])] if account else [],
        "send_timezone": (existing or {}).get("send_timezone") or "US/Eastern",
        "start": bool((existing or {}).get("start")),
        "source": "user",
    }
    mappings = [
        row for row in mappings
        if int(row.get("list_id") or 0) != int(recipient_list["id"])
    ]
    mappings.append(corrected)
    list_order = {int(row["id"]): index for index, row in enumerate(lists)}
    mappings.sort(key=lambda row: list_order.get(int(row.get("list_id") or 0), len(list_order)))
    context["mappings"] = mappings

    templates_by_id = {int(row["id"]): row for row in templates}
    lists_by_id = {int(row["id"]): row for row in lists}
    accounts_by_id = {int(row["id"]): row for row in accounts}
    context["mapping_review_rows"] = [
        {
            "template": templates_by_id[int(row["template_id"])]["name"],
            "list": lists_by_id[int(row["list_id"])]["name"],
            "account": _mapping_account_label(row, accounts_by_id),
            "recipients": lists_by_id[int(row["list_id"])].get("recipient_count") or 0,
            "source": row.get("source") or "deterministic",
        }
        for row in mappings
        if int(row.get("template_id") or 0) in templates_by_id and int(row.get("list_id") or 0) in lists_by_id
    ]

    issues = []
    mappings_by_list = {int(row["list_id"]): row for row in mappings}
    for list_record in lists:
        mapping = mappings_by_list.get(int(list_record["id"]))
        if mapping is None:
            issues.append(_template_relationship_issue(list_record["name"]))
        elif mapping.get("source") == "fallback":
            issues.append(_template_relationship_issue(list_record["name"]))
        elif accounts and not mapping.get("inbox_ids"):
            mapped_template = templates_by_id.get(int(mapping.get("template_id") or 0), {})
            issues.append(_sending_account_relationship_issue(
                mapped_template.get("name") or mapping.get("template_id"),
                list_record["name"],
            ))
    context["relationship_issues"] = list(dict.fromkeys(issues))

    requirements, values = _build_variable_state(
        templates,
        context.get("ai_hints") or {},
        context,
    )
    mapped_template_ids = {int(row["template_id"]) for row in mappings}
    context["requirements"] = [
        requirement for requirement in requirements
        if int(requirement["template_id"]) in mapped_template_ids
    ]
    context["template_values"] = values
    return {
        "mapping": corrected,
        "template": template.get("name") or "Template",
        "list": recipient_list.get("name") or "List",
        "account": account.get("source_name") if account else "a sending account that still needs to be chosen",
        "remaining_issues": context["relationship_issues"],
    }


def resolve_variable_message(context: Dict[str, Any], message: str) -> Optional[Dict[str, Any]]:
    """Interpret value assignments while treating every supplied value as opaque."""
    requirements = context.get("requirements") or []
    if not requirements:
        return None
    pending = unresolved_requirements(context)

    # This must run before pending-scope handling: in a phrase such as
    # "create them without those values", ``those`` is a reference to the
    # unresolved values, not permission to apply a stale pending assignment.
    skip_result = _explicit_skip_intent(context, message)
    if skip_result is not None:
        return skip_result

    # A previous opaque value may be waiting only for scope/category. This
    # follow-up never reinterprets or validates that stored value.
    pending_assignment = context.get("pending_assignment")
    if isinstance(pending_assignment, dict):
        pending_label = str(pending_assignment.get("label") or "").strip()
        was_unlabeled = not pending_label
        if not pending_label:
            known_categories = sorted({
                canonical_variable_name(item.get("category") or item.get("variable") or "")
                for item in pending
            })
            message_identity = _identity(message)
            pending_label = next((
                category for category in known_categories
                if _identity(category) and _identity(category) in message_identity
            ), "")
        pending_targets = _matching_variable_requirements(requirements, pending_label) if pending_label else []
        mentioned_ids = _mentioned_template_ids(pending_targets, message)
        global_scope = _has_global_scope(message)
        affirmative_scope = _is_affirmative_scope_reply(message)
        if pending_label and (global_scope or mentioned_ids or affirmative_scope):
            target_ids = {int(item["template_id"]) for item in pending_targets}
            if mentioned_ids and not global_scope:
                target_ids &= set(mentioned_ids)
            elif global_scope:
                if re.search(r"\bexcept\b", message, re.IGNORECASE) and mentioned_ids:
                    target_ids -= set(mentioned_ids)
            applied = _apply_variable_assignment(
                context,
                pending_label,
                str(pending_assignment.get("value") or ""),
                sorted(target_ids),
            )
            context["pending_assignment"] = None
            return {
                "applied": applied,
                "remaining": unresolved_requirements(context),
                "groups": variable_groups(context),
                "scope_requests": [],
            }
        if was_unlabeled and pending_label:
            unique_template_ids = sorted({int(item["template_id"]) for item in pending_targets})
            pending_assignment["label"] = pending_label
            pending_assignment["template_ids"] = unique_template_ids
            pending_assignment["templates"] = sorted({str(item.get("template") or "Template") for item in pending_targets})
            pending_assignment["count"] = len(unique_template_ids)
            if len(unique_template_ids) == 1:
                applied = _apply_variable_assignment(
                    context,
                    pending_label,
                    str(pending_assignment.get("value") or ""),
                    unique_template_ids,
                )
                context["pending_assignment"] = None
                return {
                    "applied": applied,
                    "remaining": unresolved_requirements(context),
                    "groups": variable_groups(context),
                    "scope_requests": [],
                }
            return {
                "applied": [],
                "remaining": unresolved_requirements(context),
                "groups": variable_groups(context),
                "scope_requests": [pending_assignment],
            }
        if _is_declined_scope_reply(message):
            context["pending_assignment"] = None
            return {
                "applied": [],
                "remaining": unresolved_requirements(context),
                "groups": variable_groups(context),
                "scope_requests": [],
                "scope_declined": True,
            }

    assignments: List[Tuple[str, str, List[int]]] = []
    for raw_line in message.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        tagged = re.match(r"^(?:for\s+)?(.{1,100}?)\s*(?::|=|->|\bis\b)\s*(.+)$", line, re.IGNORECASE)
        if tagged:
            label, value = tagged.group(1).strip(), tagged.group(2).strip()
            category, _scope = _assignment_scope(requirements, label)
            known_categories = {
                canonical_variable_name(item.get("category") or item.get("variable") or "")
                for item in requirements
            }
            if category in known_categories:
                assignments.append((label, value, []))
                continue
        natural = re.match(
            r"^(?:use|set|assign)\s+(.+?)\s+(?:for|as)\s+(?:all\s+|both\s+)?(.+?)(?:\s+variables?|\s+links?)?$",
            line,
            re.IGNORECASE,
        )
        if natural:
            label = natural.group(2).strip()
            value = natural.group(1).strip()
            mentioned_ids = _mentioned_template_ids(requirements, label)
            if mentioned_ids:
                mentioned_categories = {
                    canonical_variable_name(item.get("category") or item.get("variable") or "")
                    for item in requirements
                    if int(item["template_id"]) in set(mentioned_ids)
                }
                if len(mentioned_categories) == 1:
                    label = next(iter(mentioned_categories))
            assignments.append((label, value, mentioned_ids))

    if not assignments:
        pending_categories = {
            canonical_variable_name(item.get("category") or item.get("variable") or "")
            for item in pending
        }
        lowered = message.strip().casefold()
        looks_like_command = bool(re.match(
            r"^(?:please\s+)?(?:how|what|why|when|where|who|help|create|prepare|build|continue|"
            r"proceed|move\s+on|go\s+ahead|carry\s+on|finish|leave|skip|omit|ignore|approve|"
            r"review|delete|start|stop|cancel|don['’]?t|do\s+not)\b",
            lowered,
        ))
        looks_like_plain_value = (
            len(pending_categories) == 1
            and bool(message.strip())
            and "?" not in message
            and not looks_like_command
        )
        if looks_like_plain_value:
            assignments.append((next(iter(pending_categories)), message.strip(), []))
        elif pending and len(message.strip().split()) == 1 and message.strip() and "?" not in message:
            context["pending_assignment"] = {"label": None, "value": message.strip()}
            return {
                "applied": [],
                "remaining": pending,
                "groups": variable_groups(context),
                "scope_requests": [],
                "needs_category": True,
            }

    # DeepSeek handles conversational phrasings that deterministic tag parsing
    # cannot understand. Returned values must occur verbatim in the user's
    # message; deterministic code still owns scope and persistence.
    if not assignments and pending:
        try:
            from services import llm_provider

            if llm_provider.is_configured():
                inventory = [
                    {
                        "template_id": item["template_id"],
                        "template": item["template"],
                        "variable": item["variable"],
                        "category": item["category"],
                    }
                    for item in pending
                ]
                result = llm_provider.chat(
                    system=(
                        "Determine whether the message assigns opaque user-provided values to the listed "
                        "template variables, or clearly asks to leave unresolved values blank and move on. "
                        "Do not inspect, validate, rewrite, normalize, or open links. A tagged category may "
                        "apply to every matching variable. Return ONLY JSON: "
                        "{\"intent\":\"assign|skip_values|other\","
                        "\"assignments\":[{\"label\":str,\"value\":str,\"template_ids\":[int]}],"
                        "\"skip_categories\":[str],\"skip_template_ids\":[int],\"proceed\":bool}. "
                        "Only include template_ids when the message explicitly names or limits templates; "
                        "a bare tag is not explicit permission to reuse a value across templates. Each value "
                        "must be copied verbatim from the message. Use skip_values only for an explicit request "
                        "to omit/leave blank unresolved values; questions and unrelated chat are other."
                    ),
                    messages=[{
                        "role": "user",
                        "content": json.dumps({"requirements": inventory, "message": message}, ensure_ascii=False),
                    }],
                    max_tokens=1200,
                )
                parsed = _extract_json_from_text(result.text)
                if isinstance(parsed, dict) and parsed.get("intent") == "skip_values":
                    valid_categories = {
                        canonical_variable_name(item.get("category") or item.get("variable") or "")
                        for item in pending
                    }
                    skip_categories = [
                        canonical_variable_name(item)
                        for item in parsed.get("skip_categories") or []
                        if canonical_variable_name(item) in valid_categories
                    ]
                    valid_template_ids = {int(item["template_id"]) for item in pending}
                    skip_template_ids = []
                    for raw_id in parsed.get("skip_template_ids") or []:
                        try:
                            parsed_id = int(raw_id)
                        except (TypeError, ValueError):
                            continue
                        if parsed_id in valid_template_ids:
                            skip_template_ids.append(parsed_id)
                    skipped = _skip_variable_requirements(
                        context,
                        categories=skip_categories or None,
                        template_ids=skip_template_ids or None,
                    )
                    if skipped:
                        return {
                            "intent": "skip_values",
                            "skipped": skipped,
                            "remaining": unresolved_requirements(context),
                            "groups": variable_groups(context),
                            "categories": sorted({
                                canonical_variable_name(item.get("category") or item.get("variable") or "")
                                for item in skipped
                            }),
                            "template_ids": sorted({int(item["template_id"]) for item in skipped}),
                            "proceed": bool(parsed.get("proceed")) or _has_prepare_intent(message),
                        }
                for assignment in (parsed or {}).get("assignments", []) if isinstance(parsed, dict) else []:
                    if not isinstance(assignment, dict):
                        continue
                    label = str(assignment.get("label") or "").strip()
                    value = str(assignment.get("value") or "").strip()
                    if not label or not value or value not in message:
                        continue
                    template_ids = []
                    for raw_id in assignment.get("template_ids") or []:
                        try:
                            template_ids.append(int(raw_id))
                        except (TypeError, ValueError):
                            continue
                    assignments.append((label, value, template_ids))
        except Exception as exc:
            logger.warning("Variable-assignment interpretation failed: %s", exc)

    if not assignments:
        return None

    applied: List[Dict[str, Any]] = []
    scope_requests: List[Dict[str, Any]] = []
    for label, value, template_ids in assignments:
        targets = _matching_variable_requirements(requirements, label)
        mentioned_ids = template_ids or _mentioned_template_ids(targets, message)
        unique_template_ids = sorted({int(item["template_id"]) for item in targets})
        explicit_global_scope = _has_global_scope(message)
        if len(unique_template_ids) > 1 and not mentioned_ids and not explicit_global_scope:
            request = {
                "label": label,
                "value": value,
                "template_ids": unique_template_ids,
                "templates": sorted({str(item.get("template") or "Template") for item in targets}),
                "count": len(unique_template_ids),
            }
            scope_requests.append(request)
            context["pending_assignment"] = request
            continue
        target_ids = mentioned_ids or unique_template_ids
        if explicit_global_scope and re.search(r"\bexcept\b", message, re.IGNORECASE) and mentioned_ids:
            target_ids = [item for item in unique_template_ids if item not in set(mentioned_ids)]
        applied.extend(_apply_variable_assignment(context, label, value, target_ids))
    if not applied and not scope_requests:
        return None
    return {
        "applied": applied,
        "remaining": unresolved_requirements(context),
        "groups": variable_groups(context),
        "scope_requests": scope_requests,
    }


def _redis_client():
    return get_redis_client()


def save_import_context(context: Dict[str, Any]) -> None:
    conversation_id = int(context.get("conversation_id") or 0)
    if not conversation_id:
        return
    encoded = json.dumps(context)
    client = _redis_client()
    client.setex(CONTEXT_KEY.format(conversation_id=conversation_id), CONTEXT_TTL, encoded)
    if context.get("user_id") and context.get("bundle_hash"):
        client.setex(
            BUNDLE_CONTEXT_KEY.format(user_id=context["user_id"], bundle_hash=context["bundle_hash"]),
            CONTEXT_TTL,
            encoded,
        )


def _load_bundle_context(user_id: int, bundle_hash: str) -> Optional[Dict[str, Any]]:
    try:
        raw = _redis_client().get(BUNDLE_CONTEXT_KEY.format(user_id=user_id, bundle_hash=bundle_hash))
        return json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _store_staged_bundle(user_id: int, bundle_hash: str, files: Dict[str, bytes]) -> str:
    cutoff = time.time() - CONTEXT_TTL
    try:
        for entry in os.scandir(STAGING_DIR):
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                try:
                    os.remove(entry.path)
                except OSError:
                    pass
    except OSError:
        pass
    filepath = os.path.join(STAGING_DIR, f"{user_id}-{bundle_hash}.zip")
    if os.path.exists(filepath):
        os.chmod(filepath, 0o600)
        return filepath
    temporary = f"{filepath}.{uuid.uuid4().hex}.tmp"
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename in sorted(files, key=str.casefold):
            archive.writestr(filename, files[filename])
    os.replace(temporary, filepath)
    os.chmod(filepath, 0o600)
    return filepath


def _existing_resource_inventory(db: Session, user_id: int) -> Dict[str, Any]:
    accounts = db.query(SMTPAccount).filter(SMTPAccount.user_id == user_id).all()
    inboxes = db.query(Inbox).filter(Inbox.user_id == user_id).all()
    lists = db.query(RecipientList).filter(RecipientList.user_id == user_id).all()
    templates = db.query(SESEmailTemplate).filter(SESEmailTemplate.user_id == user_id).all()
    prior_hashes = []
    try:
        prefix = BUNDLE_CONTEXT_KEY.format(user_id=user_id, bundle_hash="*")
        for key in _redis_client().scan_iter(match=prefix, count=20):
            prior_hashes.append(str(key).rsplit(":", 1)[-1])
            if len(prior_hashes) >= 20:
                break
    except Exception:
        pass
    return {
        "accounts": [
            {"id": item.id, "name": item.name, "email": item.from_email, "from_name": item.from_name}
            for item in accounts
        ],
        "inboxes": [{"id": item.id, "email": item.email} for item in inboxes],
        "lists": [
            {"id": item.id, "name": item.name, "recipient_count": item.recipient_count or 0}
            for item in lists
        ],
        "templates": [
            {"id": item.id, "name": item.name, "subject": item.subject_line or ""}
            for item in templates
        ],
        "prior_bundle_hashes": prior_hashes,
    }


def _materialized_context_valid(db: Session, user_id: int, context: Dict[str, Any]) -> bool:
    checks = (
        (SMTPAccount, context.get("account_ids") or []),
        (Inbox, context.get("inbox_ids") or []),
        (RecipientList, context.get("list_ids") or []),
        (SESEmailTemplate, context.get("template_ids") or []),
    )
    for model, raw_ids in checks:
        wanted = {int(item) for item in raw_ids}
        if not wanted:
            continue
        found = {
            item[0] for item in db.query(model.id).filter(
                model.id.in_(wanted),
                model.user_id == user_id,
            ).all()
        }
        if found != wanted:
            return False
    return bool(context.get("template_ids") and context.get("list_ids"))


def _predict_import_counts(
    db: Session,
    user_id: int,
    account_specs: List[Dict[str, Any]],
    list_specs: List[Dict[str, Any]],
    template_specs: List[Dict[str, Any]],
) -> Dict[str, Dict[str, int]]:
    counts = {
        "accounts": {"created": 0, "reused": 0, "updated": 0, "failed": 0},
        "inboxes": {"created": 0, "reused": 0, "updated": 0, "failed": 0},
        "lists": {"created": 0, "reused": 0, "updated": 0, "failed": 0},
        "templates": {"created": 0, "reused": 0, "updated": 0, "failed": 0},
    }
    owned_accounts = db.query(SMTPAccount).filter(SMTPAccount.user_id == user_id).order_by(SMTPAccount.id).all()
    owned_inboxes = db.query(Inbox).filter(Inbox.user_id == user_id).order_by(Inbox.id).all()
    for account in account_specs:
        prefix = account["email_prefix"].casefold()
        existing_account = next((
            item for item in owned_accounts
            if str(item.from_email or "").casefold() == account["from_email"].casefold()
            or str(item.from_email or "").split("@", 1)[0].casefold() == prefix
        ), None)
        account_needs_update = bool(existing_account and (
            str(existing_account.from_email or "").casefold() != account["from_email"].casefold()
            or (not existing_account.from_name and account.get("from_name"))
        ))
        counts["accounts"]["updated" if account_needs_update else "reused" if existing_account else "created"] += 1
        existing_inbox = next((
            item for item in owned_inboxes
            if str(item.email or "").casefold() == account["from_email"].casefold()
        ), None)
        if existing_inbox is None and existing_account:
            existing_inbox = next((item for item in owned_inboxes if item.smtp_account_id == existing_account.id), None)
        inbox_needs_update = bool(existing_inbox and str(existing_inbox.email or "").casefold() != account["from_email"].casefold())
        counts["inboxes"]["updated" if inbox_needs_update else "reused" if existing_inbox else "created"] += 1

    owned_lists = db.query(RecipientList).filter(RecipientList.user_id == user_id).order_by(RecipientList.id).all()
    for spec in list_specs:
        existing_list = _choose_existing_by_identity(owned_lists, spec["name"])
        if existing_list is None:
            counts["lists"]["created"] += 1
            continue
        existing_recipients = {
            row.email.casefold(): row
            for row in db.query(Recipient).filter(Recipient.list_id == existing_list.id).all()
        }
        changed = False
        for row in spec.get("rows") or []:
            existing = existing_recipients.get(row["email"].casefold())
            if existing is None:
                changed = True
                break
            if any(not getattr(existing, field) and row.get(field) for field in ("first_name", "last_name", "company", "title")):
                changed = True
                break
        counts["lists"]["updated" if changed else "reused"] += 1

    owned_templates = db.query(SESEmailTemplate).filter(SESEmailTemplate.user_id == user_id).order_by(SESEmailTemplate.id).all()
    for spec in template_specs:
        candidates = [item for item in owned_templates if _identity(item.name) == spec["identity"]]
        if not candidates:
            counts["templates"]["created"] += 1
            continue
        exact = next((
            item for item in candidates
            if (item.subject_line or "") == spec["subject"]
            and (item.html_content or "") == spec["body"]
            and item.template_type == spec["template_type"]
        ), None)
        counts["templates"]["reused" if exact else "updated"] += 1
    return counts


def _summary_from_context(context: Dict[str, Any], bundle_reused: bool = False) -> Dict[str, Any]:
    counts = context.get("import_counts") or {}
    materialized = bool(context.get("materialized"))
    manifest = context.get("manifest") or {}
    if bundle_reused and materialized:
        counts = {
            "accounts": {"created": 0, "reused": len(manifest.get("accounts") or context.get("account_ids") or []), "updated": 0, "failed": 0},
            "inboxes": {"created": 0, "reused": len(context.get("inbox_ids") or []), "updated": 0, "failed": 0},
            "lists": {"created": 0, "reused": len(context.get("list_ids") or []), "updated": 0, "failed": 0},
            "templates": {"created": 0, "reused": len(context.get("template_ids") or []), "updated": 0, "failed": 0},
        }
    get_count = lambda resource, field: int((counts.get(resource) or {}).get(field) or 0)
    return {
        "accounts_created": get_count("accounts", "created"),
        "accounts_skipped": get_count("accounts", "reused"),
        "accounts_reused": get_count("accounts", "reused"),
        "accounts_updated": get_count("accounts", "updated"),
        "accounts_failed": get_count("accounts", "failed"),
        "inboxes_created": get_count("inboxes", "created"),
        "inboxes_reused": get_count("inboxes", "reused"),
        "inboxes_updated": get_count("inboxes", "updated"),
        "inboxes_failed": get_count("inboxes", "failed"),
        "lists_created": get_count("lists", "created"),
        "lists_reused": get_count("lists", "reused"),
        "lists_updated": get_count("lists", "updated"),
        "lists_failed": get_count("lists", "failed"),
        "templates_created": get_count("templates", "created"),
        "templates_reused": get_count("templates", "reused"),
        "templates_updated": get_count("templates", "updated"),
        "templates_failed": get_count("templates", "failed"),
        "orphans": list(context.get("orphans") or []),
        "errors": list(context.get("errors") or []),
        "template_ids": list(context.get("template_ids") or []),
        "list_ids": list(context.get("list_ids") or []),
        "inbox_ids": list(context.get("inbox_ids") or []),
        "requirements": list(context.get("requirements") or []),
        "unresolved_count": len(unresolved_requirements(context)),
        "mappings": list(context.get("mappings") or []),
        "import_counts": counts,
        "bundle_reused": bundle_reused,
        "materialized": materialized,
    }


def stage_import(
    db: Session,
    user: User,
    files: Dict[str, bytes],
    conversation_id: int,
) -> Dict[str, Any]:
    """Analyze and persist a bundle plan without creating database resources."""
    bundle_hash = bundle_fingerprint(files)
    prior_context = _load_bundle_context(user.id, bundle_hash)
    if prior_context and prior_context.get("materialized") and not _materialized_context_valid(db, user.id, prior_context):
        prior_context = None
    if prior_context and int(prior_context.get("user_id") or 0) == user.id:
        context = json.loads(json.dumps(prior_context))
        context["conversation_id"] = conversation_id
        if not context.get("materialized"):
            context["staged_bundle_path"] = _store_staged_bundle(user.id, bundle_hash, files)
        summary = _summary_from_context(context, bundle_reused=True)
        context["import_counts"] = summary["import_counts"]
        save_import_context(context)
        return summary

    staged_path = _store_staged_bundle(user.id, bundle_hash, files)
    plan = classify_bundle(files, use_orphan_ai=False)
    errors: List[str] = _deduplicate_plan_resources(plan)
    accounts = []
    if plan.get("accounts_file"):
        if not _universal_resend_domain():
            errors.append("accounts file: the universal Resend sender domain is not configured")
        else:
            accounts = extract_accounts(
                plan["accounts_file"],
                files[plan["accounts_file"]],
                use_ai_fallback=False,
            )

    account_records, list_records, template_records, inferred_name_count = _logical_stage_records(accounts, plan)

    file_inventory = []
    for filename, content in sorted(files.items()):
        extension = os.path.splitext(filename)[1].casefold()
        sample = ""
        if extension in {".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".html", ".htm", ".pdf", ".doc", ".docx"}:
            sample = _compact_text_sample(_extract_text_from_bytes(filename, content) or "", 900)
        file_inventory.append({
            "filename": filename,
            "extension": extension,
            "size": len(content),
            "sha256": _content_digest(content),
            "sample": sample,
        })
    existing_resources = _existing_resource_inventory(db, user.id)
    ai_hints = _ai_bundle_hints(
        account_records,
        list_records,
        template_records,
        file_inventory,
        existing_resources,
    )
    roles_changed = _apply_ai_file_roles(plan, files, ai_hints)
    roles_changed = _promote_document_orphans(plan, files) or roles_changed
    errors.extend(
        issue for issue in _deduplicate_plan_resources(plan)
        if issue not in errors
    )
    if roles_changed and not accounts and plan.get("accounts_file"):
        if not _universal_resend_domain():
            domain_error = "accounts file: the universal Resend sender domain is not configured"
            if domain_error not in errors:
                errors.append(domain_error)
        else:
            accounts = extract_accounts(
                plan["accounts_file"],
                files[plan["accounts_file"]],
                use_ai_fallback=False,
            )
    if not accounts and plan.get("accounts_file") and _universal_resend_domain():
        seen_emails = set()
        for raw_account in ai_hints.get("extracted_accounts") or []:
            if not isinstance(raw_account, dict):
                continue
            normalized = _normalize_resend_account(raw_account)
            if not normalized or normalized["from_email"] in seen_emails:
                continue
            seen_emails.add(normalized["from_email"])
            accounts.append(normalized)
    account_records, list_records, template_records, inferred_name_count = _logical_stage_records(accounts, plan)
    if plan.get("accounts_file") and not accounts and not errors:
        errors.append("accounts file: no valid sending accounts could be extracted")
    if not accounts and not existing_resources.get("inboxes") and not errors:
        errors.append("bundle: no sending account or existing inbox is available")
    if not list_records:
        errors.append("bundle: no valid recipient list could be extracted")
    if not template_records:
        errors.append("bundle: no valid email template could be extracted")
    mappings = _build_smart_mappings(
        account_records,
        list_records,
        template_records,
        ai_hints,
        plan.get("automation_rows") or [],
    )
    accounts_by_id = {item["id"]: item for item in account_records}
    lists_by_id = {item["id"]: item for item in list_records}
    templates_by_id = {item["id"]: item for item in template_records}
    mapping_review_rows = [
        {
            "template": templates_by_id[mapping["template_id"]]["name"],
            "list": lists_by_id[mapping["list_id"]]["name"],
            "account": _mapping_account_label(mapping, accounts_by_id),
            "recipients": lists_by_id[mapping["list_id"]]["recipient_count"],
            "source": mapping.get("source") or "deterministic",
        }
        for mapping in mappings
        if mapping.get("template_id") in templates_by_id and mapping.get("list_id") in lists_by_id
    ]
    automation_mapped_lists = {
        int(mapping["list_id"])
        for mapping in mappings
        if mapping.get("source") == "automation"
    }
    relationship_issues = [] if len(automation_mapped_lists) == len(list_records) else [
        friendly_relationship_issue(issue)
        for issue in ai_hints.get("unclear") or []
        if str(issue).strip()
    ]
    for mapping in mappings:
        template = templates_by_id.get(mapping.get("template_id"), {})
        recipient_list = lists_by_id.get(mapping.get("list_id"), {})
        if mapping.get("source") == "fallback":
            relationship_issues.append(_template_relationship_issue(
                recipient_list.get("name") or mapping.get("list_id")
            ))
        if account_records and not mapping.get("inbox_ids"):
            relationship_issues.append(_sending_account_relationship_issue(
                template.get("name") or mapping.get("template_id"),
                recipient_list.get("name"),
            ))
    if len(mappings) < len(list_records):
        relationship_issues.append(
            "I couldn't find an email template for one or more contact lists. "
            "Please tell me which template each list should use before I continue."
        )
    asset_summary = {"attachment": 0, "link": 0, "mixed": 0, "plain": 0}
    for hint in ai_hints.get("asset_modes") or []:
        if not isinstance(hint, dict):
            continue
        try:
            hinted_template_id = int(hint.get("template_id") or 0)
        except (TypeError, ValueError):
            continue
        mode = str(hint.get("mode") or "").casefold()
        if mode in asset_summary and 1 <= hinted_template_id <= len(template_records):
            template_records[hinted_template_id - 1]["asset_mode"] = mode
    for template in template_records:
        has_attachment = bool(template.get("attachment_names"))
        has_link = any(variable_category(variable) in {"linkedin", "microsoft"} or "link" in variable for variable in template["variables"])
        mode = template.get("asset_mode") or ("mixed" if has_attachment and has_link else "attachment" if has_attachment else "link" if has_link else "plain")
        asset_summary[mode] += 1
    requirements, template_values = _build_variable_state(template_records, ai_hints)
    mapped_template_ids = {int(mapping["template_id"]) for mapping in mappings}
    requirements = [
        requirement for requirement in requirements
        if int(requirement["template_id"]) in mapped_template_ids
    ]
    import_counts = _predict_import_counts(db, user.id, account_records, list_records, template_records)
    context = {
        "version": IMPORT_VERSION,
        "conversation_id": conversation_id,
        "user_id": user.id,
        "bundle_hash": bundle_hash,
        "staged_bundle_path": staged_path,
        "materialized": False,
        "template_ids": [],
        "list_ids": [],
        "inbox_ids": [],
        "account_ids": [],
        "automation_rows": plan.get("automation_rows") or [],
        "manifest": {
            "files": file_inventory,
            "accounts": account_records,
            "lists": [
                {key: value for key, value in item.items() if key != "rows"}
                for item in list_records
            ],
            "templates": template_records,
        },
        "ai_hints": ai_hints,
        "mappings": mappings,
        "mapping_review_rows": mapping_review_rows,
        "relationship_issues": relationship_issues,
        "asset_summary": asset_summary,
        "inferred_name_count": inferred_name_count,
        "requirements": requirements,
        "template_values": template_values,
        "pending_assignment": None,
        "import_counts": import_counts,
        "orphans": plan["orphans"],
        "errors": errors,
    }
    save_import_context(context)
    return _summary_from_context(context)


def _translate_staged_state(
    staged_context: Dict[str, Any],
    account_records: List[Dict[str, Any]],
    list_records: List[Dict[str, Any]],
    template_records: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    manifest = staged_context.get("manifest") or {}
    staged_accounts = {int(item["id"]): item for item in manifest.get("accounts") or []}
    staged_lists = {int(item["id"]): item for item in manifest.get("lists") or []}
    staged_templates = {int(item["id"]): item for item in manifest.get("templates") or []}

    account_id_map = {}
    inbox_id_map = {}
    for staged_id, staged in staged_accounts.items():
        actual = next((
            row for row in account_records
            if str(row.get("email_prefix") or "").casefold() == str(staged.get("email_prefix") or "").casefold()
        ), None)
        if actual:
            account_id_map[staged_id] = actual["id"]
            inbox_id_map[int(staged.get("inbox_id") or staged_id)] = actual.get("inbox_id")

    list_id_map = {}
    for staged_id, staged in staged_lists.items():
        actual = next((
            row for row in list_records
            if _identity(row.get("source_name") or row.get("name")) == _identity(staged.get("source_name") or staged.get("name"))
        ), None)
        if actual:
            list_id_map[staged_id] = actual["id"]

    template_id_map = {}
    for staged_id, staged in staged_templates.items():
        actual = next((
            row for row in template_records
            if row.get("identity") == staged.get("identity")
        ), None)
        if actual:
            template_id_map[staged_id] = actual["id"]

    mappings = []
    for staged in staged_context.get("mappings") or []:
        try:
            template_id = template_id_map.get(int(staged["template_id"]))
            list_id = list_id_map.get(int(staged["list_id"]))
            account_id = account_id_map.get(int(staged["account_id"])) if staged.get("account_id") is not None else None
        except (KeyError, TypeError, ValueError):
            continue
        if template_id is None or list_id is None:
            continue
        inbox_ids = []
        for staged_inbox_id in staged.get("inbox_ids") or []:
            try:
                actual_inbox_id = inbox_id_map.get(int(staged_inbox_id))
            except (TypeError, ValueError):
                continue
            if actual_inbox_id and actual_inbox_id not in inbox_ids:
                inbox_ids.append(actual_inbox_id)
        mappings.append({
            **staged,
            "template_id": template_id,
            "list_id": list_id,
            "account_id": account_id,
            "inbox_ids": inbox_ids,
        })

    translated_values: Dict[str, Dict[str, str]] = {}
    staged_values = staged_context.get("template_values") or {}
    for staged_template_id, actual_template_id in template_id_map.items():
        translated_values[str(actual_template_id)] = dict(staged_values.get(str(staged_template_id)) or {})

    translated_requirements = []
    for staged_requirement in staged_context.get("requirements") or []:
        try:
            actual_template_id = template_id_map.get(int(staged_requirement.get("template_id")))
        except (TypeError, ValueError):
            continue
        if actual_template_id is None:
            continue
        variable = canonical_variable_name(staged_requirement.get("variable") or "")
        translated_requirements.append({
            **staged_requirement,
            "id": f"{actual_template_id}:{variable}",
            "template_id": actual_template_id,
            "variable": variable,
        })

    translated_categories = []
    for hint in (staged_context.get("ai_hints") or {}).get("variable_categories") or []:
        if not isinstance(hint, dict):
            continue
        try:
            actual_template_id = template_id_map.get(int(hint.get("template_id")))
        except (TypeError, ValueError):
            continue
        if actual_template_id:
            translated_categories.append({**hint, "template_id": actual_template_id})
    translated_hints = {"variable_categories": translated_categories, "mappings": []}
    return mappings, {
        "template_values": translated_values,
        "requirements": translated_requirements,
    }, translated_hints


def execute_import(
    db: Session,
    user: User,
    files: Dict[str, bytes],
    conversation_id: int,
    staged_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Idempotently create/reuse accounts → inboxes → lists → templates."""
    plan = classify_bundle(files, use_orphan_ai=not bool(staged_context))
    plan_issues = _deduplicate_plan_resources(plan)
    if staged_context:
        _apply_ai_file_roles(plan, files, staged_context.get("ai_hints") or {})
        _promote_document_orphans(plan, files)
        plan_issues.extend(
            issue for issue in _deduplicate_plan_resources(plan)
            if issue not in plan_issues
        )
    bundle_hash = bundle_fingerprint(files)
    previous_context = staged_context or _load_bundle_context(user.id, bundle_hash) or {}
    summary: Dict[str, Any] = {
        "accounts_created": 0,
        "accounts_skipped": 0,
        "accounts_reused": 0,
        "accounts_updated": 0,
        "accounts_failed": 0,
        "inboxes_created": 0,
        "inboxes_reused": 0,
        "inboxes_updated": 0,
        "inboxes_failed": 0,
        "lists_created": 0,
        "lists_reused": 0,
        "lists_updated": 0,
        "lists_failed": 0,
        "templates_created": 0,
        "templates_reused": 0,
        "templates_updated": 0,
        "templates_failed": 0,
        "orphans": plan["orphans"],
        "errors": list(plan_issues),
        "template_ids": [],
        "list_ids": [],
        "inbox_ids": [],
    }
    account_records: List[Dict[str, Any]] = []
    list_records: List[Dict[str, Any]] = []
    template_records: List[Dict[str, Any]] = []

    # ── Resend accounts → inboxes ──
    accounts: List[Dict[str, str]] = []
    if plan["accounts_file"]:
        accounts = extract_accounts(
            plan["accounts_file"],
            files[plan["accounts_file"]],
            use_ai_fallback=not bool(staged_context),
        )
        if not accounts and staged_context:
            for staged_account in (staged_context.get("manifest") or {}).get("accounts") or []:
                normalized = _normalize_resend_account(staged_account)
                if normalized:
                    accounts.append(normalized)
        if not accounts:
            summary["errors"].append("accounts file: no valid sending accounts could be extracted")
            summary["accounts_failed"] += 1
        for acc in accounts:
            try:
                owned_accounts = db.query(SMTPAccount).filter(SMTPAccount.user_id == user.id).order_by(SMTPAccount.id).all()
                prefix = acc["email_prefix"].casefold()
                existing = next((
                    item for item in owned_accounts
                    if str(item.from_email or "").casefold() == acc["from_email"].casefold()
                    or (
                        str(item.provider_type or "").casefold() in {
                            "brevo", "providertype.brevo", "ses_api", "providertype.ses_api"
                        }
                        and str(item.from_email or "").split("@", 1)[0].casefold() == prefix
                    )
                ), None)
                if existing is not None:
                    summary["accounts_skipped"] += 1
                    smtp = existing
                    account_updated = False
                    if str(smtp.from_email or "").casefold() != acc["from_email"].casefold():
                        smtp.from_email = acc["from_email"]
                        account_updated = True
                    if not smtp.from_name and acc["from_name"]:
                        smtp.from_name = acc["from_name"]
                        account_updated = True
                    summary["accounts_updated" if account_updated else "accounts_reused"] += 1
                else:
                    smtp = SMTPAccount(
                        user_id=user.id,
                        name=_assistant_account_name(owned_accounts, acc["name"], prefix),
                        host="resend-api",
                        port=443,
                        username="resend",
                        password="resend",
                        encryption=EncryptionType.TLS,
                        from_email=acc["from_email"],
                        from_name=acc["from_name"] or None,
                        provider_type="brevo",
                    )
                    db.add(smtp)
                    db.flush()
                    summary["accounts_created"] += 1

                inbox = db.query(Inbox).filter(
                    Inbox.user_id == user.id,
                    func.lower(Inbox.email) == acc["from_email"].casefold(),
                ).first()
                inbox_updated = False
                if inbox is None and existing is not None:
                    inbox = db.query(Inbox).filter(
                        Inbox.user_id == user.id,
                        Inbox.smtp_account_id == smtp.id,
                    ).order_by(Inbox.id).first()
                    if inbox is not None and str(inbox.email or "").casefold() != acc["from_email"].casefold():
                        inbox.email = acc["from_email"]
                        inbox_updated = True
                if inbox is None:
                    inbox = Inbox(user_id=user.id, email=acc["from_email"], smtp_account_id=smtp.id)
                    db.add(inbox)
                    db.flush()
                    summary["inboxes_created"] += 1
                else:
                    summary["inboxes_updated" if inbox_updated else "inboxes_reused"] += 1
                db.commit()
                if inbox.id not in summary["inbox_ids"]:
                    summary["inbox_ids"].append(inbox.id)
                account_records.append({
                    "id": smtp.id,
                    "source_name": acc["name"],
                    "name": smtp.name,
                    "from_name": smtp.from_name or "",
                    "email_prefix": prefix,
                    "from_email": acc["from_email"],
                    "inbox_id": inbox.id,
                })
            except Exception as e:
                db.rollback()
                summary["accounts_failed"] += 1
                summary["errors"].append(f"account {acc.get('from_email')}: {e}")

    # ── Lists ──
    for lst in plan["lists"]:
        try:
            if not lst["rows"]:
                continue
            existing_lists = db.query(RecipientList).filter(RecipientList.user_id == user.id).order_by(RecipientList.id).all()
            rl = _choose_existing_by_identity(existing_lists, lst["name"])
            created = rl is None
            if created:
                rl = RecipientList(user_id=user.id, name=lst["name"])
                db.add(rl)
                db.flush()
            existing_recipients = {
                recipient.email.casefold(): recipient
                for recipient in db.query(Recipient).filter(Recipient.list_id == rl.id).all()
            }
            changed = False
            seen_import_emails = set()
            for row in lst["rows"]:
                normalized_email = row["email"].casefold()
                if normalized_email in seen_import_emails:
                    continue
                seen_import_emails.add(normalized_email)
                existing_recipient = existing_recipients.get(normalized_email)
                if existing_recipient:
                    for field in ("first_name", "last_name", "company", "title"):
                        if not getattr(existing_recipient, field) and row.get(field):
                            setattr(existing_recipient, field, row[field])
                            changed = True
                    continue
                db.add(Recipient(
                    list_id=rl.id,
                    email=row["email"],
                    first_name=row["first_name"] or None,
                    last_name=row["last_name"] or None,
                    company=row["company"] or None,
                    title=row["title"] or None,
                    status=RecipientStatus.ACTIVE,
                ))
                changed = True
            db.flush()
            actual_count = db.query(Recipient).filter(Recipient.list_id == rl.id).count()
            rl.recipient_count = actual_count
            rl.active_count = db.query(Recipient).filter(
                Recipient.list_id == rl.id,
                Recipient.status == RecipientStatus.ACTIVE,
            ).count()
            db.commit()
            if created:
                summary["lists_created"] += 1
            elif changed:
                summary["lists_updated"] += 1
            else:
                summary["lists_reused"] += 1
            summary["list_ids"].append(rl.id)
            list_records.append({
                "id": rl.id,
                "name": rl.name,
                "source_name": lst["name"],
                "recipient_count": actual_count,
            })
        except Exception as e:
            db.rollback()
            summary["lists_failed"] += 1
            summary["errors"].append(f"list {lst['name']}: {e}")

    # ── Templates (+attachments) ──
    for tpl in plan["templates"]:
        try:
            existing_templates = db.query(SESEmailTemplate).filter(SESEmailTemplate.user_id == user.id).order_by(SESEmailTemplate.id).all()
            candidates = [item for item in existing_templates if _identity(item.name) == tpl.get("identity", _identity(tpl["name"]))]
            exact_content = [
                item for item in candidates
                if (item.subject_line or "") == tpl["subject"] and (item.html_content or "") == tpl["body"]
            ]
            t = min(
                exact_content or candidates,
                key=lambda item: (item.template_type != tpl["template_type"], item.id),
                default=None,
            )
            created = t is None
            updated = False
            if created:
                t = SESEmailTemplate(
                    user_id=user.id,
                    name=tpl["name"],
                    subject_line=tpl["subject"],
                    html_content=tpl["body"],
                    text_fallback=None if tpl["template_type"] == "html" else tpl["body"],
                    template_type=tpl["template_type"],
                )
                db.add(t)
                db.flush()
                existing_templates.append(t)
            elif (t.subject_line or "") != tpl["subject"] or (t.html_content or "") != tpl["body"] or t.template_type != tpl["template_type"]:
                t.subject_line = tpl["subject"]
                t.html_content = tpl["body"]
                t.text_fallback = None if tpl["template_type"] == "html" else tpl["body"]
                t.template_type = tpl["template_type"]
                updated = True

            atts = list(t.attachments or [])
            for filename, content in tpl.get("attachments", []):
                if len(content) > MAX_FILE_BYTES:
                    continue
                reusable = _reusable_attachment(existing_templates, filename, content)
                attachment = reusable or _store_attachment(filename, content)
                if reusable is None:
                    wanted_name = _safe_name(filename).casefold()
                    atts = [
                        current for current in atts
                        if _safe_name(str(current.get("filename") or "")).casefold() != wanted_name
                    ]
                duplicate = any(
                    str(current.get("stored_name") or "") == str(attachment.get("stored_name") or "")
                    for current in atts
                )
                if not duplicate:
                    atts.append(attachment)
                    updated = updated or not created
            variables = extract_template_variables(t.subject_line, t.html_content, t.text_fallback or "")
            if t.available_variables != variables:
                t.available_variables = variables
                updated = updated or not created
            t.attachments = atts or None
            if updated and not created:
                t.version = int(t.version or 1) + 1
            db.commit()
            if created:
                summary["templates_created"] += 1
            elif updated:
                summary["templates_updated"] += 1
            else:
                summary["templates_reused"] += 1
            summary["template_ids"].append(t.id)
            template_records.append({
                "id": t.id,
                "name": t.name,
                "source_name": tpl.get("filename") or t.name,
                "identity": tpl.get("identity") or _identity(t.name),
                "subject": t.subject_line or "",
                "body": t.html_content or "",
                "variables": variables,
                "attachment_names": [str(item.get("filename") or "") for item in (t.attachments or [])],
            })
        except Exception as e:
            db.rollback()
            summary["templates_failed"] += 1
            summary["errors"].append(f"template {tpl['name']}: {e}")

    # ── Materialize the staged semantic plan (or analyze for legacy callers) ──
    if staged_context:
        mappings, translated_previous, ai_hints = _translate_staged_state(
            staged_context,
            account_records,
            list_records,
            template_records,
        )
        if not mappings:
            mappings = _build_smart_mappings(
                account_records,
                list_records,
                template_records,
                ai_hints,
                plan.get("automation_rows") or [],
            )
        previous_for_values = translated_previous
    else:
        ai_hints = _ai_bundle_hints(account_records, list_records, template_records)
        mappings = _build_smart_mappings(
            account_records,
            list_records,
            template_records,
            ai_hints,
            plan.get("automation_rows") or [],
        )
        previous_for_values = previous_context
    requirements, template_values = _build_variable_state(template_records, ai_hints, previous_for_values)
    mapped_template_ids = {int(mapping["template_id"]) for mapping in mappings}
    requirements = [
        requirement for requirement in requirements
        if int(requirement["template_id"]) in mapped_template_ids
    ]
    context = {
        "version": IMPORT_VERSION,
        "conversation_id": conversation_id,
        "user_id": user.id,
        "bundle_hash": bundle_hash,
        "materialized": True,
        "template_ids": summary["template_ids"],
        "list_ids": summary["list_ids"],
        "inbox_ids": summary["inbox_ids"],
        "account_ids": [row["id"] for row in account_records],
        "automation_rows": plan.get("automation_rows") or [],
        "mappings": mappings,
        "requirements": requirements,
        "template_values": template_values,
        "pending_assignment": None,
        "manifest": staged_context.get("manifest") if staged_context else None,
        "ai_hints": staged_context.get("ai_hints") if staged_context else ai_hints,
        "mapping_review_rows": staged_context.get("mapping_review_rows") if staged_context else [],
        "asset_summary": staged_context.get("asset_summary") if staged_context else {},
        "inferred_name_count": staged_context.get("inferred_name_count") if staged_context else 0,
        "relationship_issues": staged_context.get("relationship_issues") if staged_context else [],
        "import_counts": {
            "accounts": {"created": summary["accounts_created"], "reused": summary["accounts_reused"], "updated": summary["accounts_updated"], "failed": summary["accounts_failed"]},
            "inboxes": {"created": summary["inboxes_created"], "reused": summary["inboxes_reused"], "updated": summary["inboxes_updated"], "failed": summary["inboxes_failed"]},
            "lists": {"created": summary["lists_created"], "reused": summary["lists_reused"], "updated": summary["lists_updated"], "failed": summary["lists_failed"]},
            "templates": {"created": summary["templates_created"], "reused": summary["templates_reused"], "updated": summary["templates_updated"], "failed": summary["templates_failed"]},
        },
        "orphans": plan["orphans"],
        "errors": summary["errors"],
    }
    if conversation_id:
        try:
            save_import_context(context)
        except Exception as e:
            logger.warning(f"Failed to store import context: {e}")

    summary["requirements"] = requirements
    summary["unresolved_count"] = sum(1 for item in requirements if not item.get("resolved"))
    summary["mappings"] = mappings

    return summary


def load_import_context(conversation_id: int) -> Optional[Dict[str, Any]]:
    raw = _redis_client().get(CONTEXT_KEY.format(conversation_id=conversation_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def materialize_staged_import(
    db: Session,
    user: User,
    context: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Create/reuse resources for a reviewed stage and return summary + context."""
    if int(context.get("user_id") or 0) != user.id:
        raise ValueError("Bundle context does not belong to this user")
    if context.get("materialized"):
        return _summary_from_context(context, bundle_reused=True), context

    latest = _load_bundle_context(user.id, str(context.get("bundle_hash") or ""))
    if latest and latest.get("materialized") and _materialized_context_valid(db, user.id, latest):
        latest = json.loads(json.dumps(latest))
        latest["conversation_id"] = context.get("conversation_id")
        save_import_context(latest)
        return _summary_from_context(latest, bundle_reused=True), latest

    staged_path = str(context.get("staged_bundle_path") or "")
    if not staged_path or not os.path.isfile(staged_path):
        raise ValueError("The staged bundle expired; upload it again")
    with open(staged_path, "rb") as staged_file:
        files = unzip_bundle(staged_file.read())
    if bundle_fingerprint(files) != context.get("bundle_hash"):
        raise ValueError("The staged bundle fingerprint does not match")

    summary = execute_import(
        db,
        user,
        files,
        int(context.get("conversation_id") or 0),
        staged_context=context,
    )
    materialized_context = load_import_context(int(context.get("conversation_id") or 0))
    if not materialized_context:
        raise ValueError("The materialized import context could not be saved")
    try:
        os.remove(staged_path)
    except OSError:
        pass
    return summary, materialized_context


# ═════════════════════════════════════════════════════════════════════════
# Campaign generation
# ═════════════════════════════════════════════════════════════════════════

def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def parse_automation_csv(content: bytes) -> List[Dict[str, str]]:
    rows = []
    reader = csv.DictReader(io.StringIO(_decode(content)))
    for row in reader:
        if not (row.get("template") and row.get("list")):
            continue
        rows.append({
            "template": (row.get("template") or "").strip(),
            "list": (row.get("list") or "").strip(),
            "inboxes": (row.get("inboxes") or "").strip(),
            "send_timezone": (row.get("send_timezone") or "US/Eastern").strip(),
            "start": (row.get("start") or "").strip().lower() in {"yes", "true", "1"},
        })
    return rows


def build_campaign_plan(
    templates: List[SESEmailTemplate],
    lists: List[RecipientList],
    inboxes: List[Inbox],
    automation_rows: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, Any]]:
    """One campaign per list. Templates reusable, lists never reused.

    automation_rows (from automation.csv) win when provided; otherwise
    name-match templates↔lists first, then round-robin the leftovers.
    """
    if not lists or not templates:
        return []

    tpl_by_norm = {_norm(t.name): t for t in templates}
    list_by_norm = {_norm(l.name): l for l in lists}

    assignments: Dict[int, Any] = {}  # list id -> {template, start, timezone, inboxes}

    if automation_rows:
        for row in automation_rows:
            t = tpl_by_norm.get(_norm(row["template"]))
            l = list_by_norm.get(_norm(row["list"]))
            if not t or not l:
                continue
            inbox_spec = row["inboxes"]
            if inbox_spec and inbox_spec.lower() != "warmed":
                wanted = {_norm(x) for x in inbox_spec.split(",") if x.strip()}
                inbox_list = [i for i in inboxes if _norm(i.email) in wanted or _norm(i.email.split("@")[0]) in wanted]
            else:
                inbox_list = list(inboxes)
            if not inbox_list:
                inbox_list = list(inboxes)
            assignments[l.id] = {
                "template": t,
                "inboxes": [i.id for i in inbox_list],
                "send_timezone": row["send_timezone"],
                "start": row["start"],
            }
        # any list not covered by automation → name-match/round-robin with defaults
        remaining_lists = [l for l in lists if l.id not in assignments]
    else:
        remaining_lists = list(lists)

    if remaining_lists:
        # name-match first
        for l in list(remaining_lists):
            t = tpl_by_norm.get(_norm(l.name))
            if t:
                assignments[l.id] = {
                    "template": t,
                    "inboxes": [i.id for i in inboxes],
                    "send_timezone": "US/Eastern",
                    "start": False,
                }
                remaining_lists.remove(l)
        # round-robin the rest — prefer templates that haven't been used yet,
        # then reuse all templates (lists are still used exactly once).
        used_norm = {_norm(a["template"].name) for a in assignments.values()}
        unused = [t for t in templates if _norm(t.name) not in used_norm]
        for idx, l in enumerate(remaining_lists):
            pool = unused if idx < len(unused) else templates
            t = pool[idx % len(pool)] if idx < len(unused) else pool[(idx - len(unused)) % len(pool)]
            assignments[l.id] = {
                "template": t,
                "inboxes": [i.id for i in inboxes],
                "send_timezone": "US/Eastern",
                "start": False,
            }

    plan = []
    for l in lists:
        if l.id not in assignments:
            continue
        a = assignments[l.id]
        plan.append({
            "list": l,
            "template": a["template"],
            "inbox_ids": a["inboxes"] or [i.id for i in inboxes],
            "send_timezone": a["send_timezone"],
            "start": a["start"],
        })
    return plan
