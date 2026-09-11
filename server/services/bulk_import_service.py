"""
Bulk Import Service — deterministic, staged import pipeline.

Supported file types:
  .csv   — single import type auto-detected by headers
    .xlsx  — multi-sheet workbook (sheets: resend_accounts, smtp_accounts, inboxes, lists)
  .txt   — list + recipients only (List Name: / Description: / recipient rows)
  .pdf   — list + recipients only (same text structure as .txt)
    .json  — Resend accounts only, from a contact-audit export (top-level list of contacts,
           or an object with a "files" list of {company, contact_name, contact_email, ...})

Parse → validate → preview → (caller commits after user confirmation)
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes — one per entity type
# ---------------------------------------------------------------------------

@dataclass
class ParsedResendAccount:
    account_name: str
    from_email_prefix: str
    from_name: str
    aws_region: str = ""
    row_number: int = 0


@dataclass
class ParsedSMTPAccount:
    account_name: str
    from_email: str
    from_name: str
    host: str
    port: int
    username: str
    password: str
    encryption: str = "tls"
    hourly_limit: int = 100
    daily_limit: int = 1000
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    row_number: int = 0


@dataclass
class ParsedInbox:
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
    row_number: int = 0


@dataclass
class ParsedRecipient:
    email: str
    first_name: str = ""
    last_name: str = ""


@dataclass
class ParsedList:
    list_name: str
    list_description: str = ""
    recipients: List[ParsedRecipient] = field(default_factory=list)


@dataclass
class ParseResult:
    ses_accounts: List[ParsedResendAccount] = field(default_factory=list)
    smtp_accounts: List[ParsedSMTPAccount] = field(default_factory=list)
    inboxes: List[ParsedInbox] = field(default_factory=list)
    lists: List[ParsedList] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def preview(self) -> Dict[str, Any]:
        """Return a safe summary — no passwords, no secrets."""
        total_recipients = sum(len(lst.recipients) for lst in self.lists)
        return {
            "ses_accounts": len(self.ses_accounts),
            "smtp_accounts": len(self.smtp_accounts),
            "inboxes": len(self.inboxes),
            "lists": len(self.lists),
            "total_recipients": total_recipients,
            "errors": self.errors,
            "warnings": self.warnings,
            "valid": self.is_valid,
            "ses_preview": [
                {"account_name": a.account_name, "from_email_prefix": a.from_email_prefix, "from_name": a.from_name}
                for a in self.ses_accounts
            ],
            "smtp_preview": [
                {"account_name": a.account_name, "from_email": a.from_email, "from_name": a.from_name,
                 "host": a.host, "port": a.port, "has_imap": bool(a.imap_host)}
                for a in self.smtp_accounts
            ],
            "inbox_preview": [
                {"email": i.email, "account_name": i.account_name, "group": i.group,
                 "start_warmup": i.start_warmup}
                for i in self.inboxes
            ],
            "list_preview": [
                {"list_name": lst.list_name, "list_description": lst.list_description,
                 "recipient_count": len(lst.recipients)}
                for lst in self.lists
            ],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

def _valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email.strip()))


def _clean(val: Any) -> str:
    return str(val).strip() if val is not None else ""


def _int(val: Any, default: int) -> int:
    try:
        return int(str(val).strip())
    except Exception:
        return default


def _bool(val: Any, default: bool = True) -> bool:
    if val is None:
        return default
    s = str(val).strip().lower()
    if s in ("true", "yes", "1"):
        return True
    if s in ("false", "no", "0"):
        return False
    return default


def _normalize_header(h: str) -> str:
    """Lowercase, strip, collapse whitespace/hyphens to underscore."""
    return re.sub(r'[\s\-]+', '_', h.lower().strip())


def _normalize_headers(row: dict) -> dict:
    return {_normalize_header(k): v for k, v in row.items()}


# ---------------------------------------------------------------------------
# CSV row validators
# ---------------------------------------------------------------------------

def _parse_resend_row(row: dict, row_num: int, errors: List[str]) -> Optional[ParsedResendAccount]:
    r = _normalize_headers(row)
    name = _clean(r.get("account_name"))
    prefix = _clean(r.get("from_email_prefix") or r.get("from_email_prefix") or r.get("email_prefix") or "")
    from_name = _clean(r.get("from_name"))

    missing = [f for f, v in [("account_name", name), ("from_email_prefix", prefix), ("from_name", from_name)] if not v]
    if missing:
        errors.append(f"Resend row {row_num}: missing required fields: {', '.join(missing)}")
        return None

    return ParsedResendAccount(
        account_name=name,
        from_email_prefix=prefix,
        from_name=from_name,
        aws_region=_clean(r.get("aws_region") or ""),
        row_number=row_num,
    )


def _parse_smtp_row(row: dict, row_num: int, errors: List[str]) -> Optional[ParsedSMTPAccount]:
    r = _normalize_headers(row)
    required = {
        "account_name": _clean(r.get("account_name") or r.get("name") or ""),
        "from_email": _clean(r.get("from_email") or ""),
        "from_name": _clean(r.get("from_name") or ""),
        "host": _clean(r.get("host") or ""),
        "username": _clean(r.get("username") or ""),
        "password": _clean(r.get("password") or ""),
        "encryption": _clean(r.get("encryption") or "tls"),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        errors.append(f"SMTP row {row_num}: missing required fields: {', '.join(missing)}")
        return None

    if not _valid_email(required["from_email"]):
        errors.append(f"SMTP row {row_num}: invalid from_email '{required['from_email']}'")
        return None

    enc = required["encryption"].lower()
    if enc not in ("tls", "ssl", "none"):
        enc = "tls"

    return ParsedSMTPAccount(
        account_name=required["account_name"],
        from_email=required["from_email"],
        from_name=required["from_name"],
        host=required["host"],
        port=_int(r.get("port"), 587),
        username=required["username"],
        password=required["password"],
        encryption=enc,
        hourly_limit=_int(r.get("hourly_limit"), 100),
        daily_limit=_int(r.get("daily_limit"), 1000),
        imap_host=_clean(r.get("imap_host") or ""),
        imap_port=_int(r.get("imap_port"), 993),
        imap_username=_clean(r.get("imap_username") or ""),
        imap_password=_clean(r.get("imap_password") or ""),
        row_number=row_num,
    )


def _parse_inbox_row(row: dict, row_num: int, errors: List[str]) -> Optional[ParsedInbox]:
    r = _normalize_headers(row)
    email = _clean(r.get("email") or "")
    account_name = _clean(r.get("account_name") or r.get("account") or "")

    if not email:
        errors.append(f"Inbox row {row_num}: missing email")
        return None
    if not _valid_email(email):
        errors.append(f"Inbox row {row_num}: invalid email '{email}'")
        return None
    if not account_name:
        errors.append(f"Inbox row {row_num}: missing account_name")
        return None

    group = _clean(r.get("group") or "A").upper()
    if group not in ("A", "B", "C"):
        group = "A"

    reply_enabled_raw = r.get("reply_enabled")
    reply_enabled = _bool(reply_enabled_raw, True) if reply_enabled_raw else None

    return ParsedInbox(
        email=email,
        account_name=account_name,
        group=group,
        reply_to_email=_clean(r.get("reply_to_email") or ""),
        imap_host=_clean(r.get("imap_host") or ""),
        imap_port=_int(r.get("imap_port"), 993),
        imap_username=_clean(r.get("imap_username") or ""),
        imap_password=_clean(r.get("imap_password") or ""),
        reply_enabled=reply_enabled,
        start_warmup=_bool(r.get("start_warmup"), True),
        row_number=row_num,
    )


def _parse_recipient_line(line: str) -> Optional[ParsedRecipient]:
    """Parse a comma-separated recipient line: email[, first_name[, last_name]]"""
    parts = [p.strip() for p in line.split(",")]
    if not parts:
        return None
    email = parts[0].strip()
    if not _valid_email(email):
        return None
    first = parts[1].strip() if len(parts) > 1 else ""
    last = parts[2].strip() if len(parts) > 2 else ""
    return ParsedRecipient(email=email, first_name=first, last_name=last)


# ---------------------------------------------------------------------------
# Auto-detect which CSV type by examining its headers
# ---------------------------------------------------------------------------

_RESEND_HEADERS = {"from_email_prefix", "email_prefix"}
_SMTP_HEADERS = {"host", "password"}
_INBOX_HEADERS = {"account_name", "account"}
_LIST_HEADERS = {"list_name", "list_description"}
_RECIPIENT_IN_LIST_HEADERS = {"list_name"}


def _detect_csv_type(headers: List[str]) -> str:
    """Return 'ses_accounts', 'smtp_accounts', 'inboxes', 'lists', or 'unknown'."""
    h = {_normalize_header(x) for x in headers}
    if _RESEND_HEADERS & h and "host" not in h:
        return "ses_accounts"
    if _SMTP_HEADERS & h:
        return "smtp_accounts"
    if _LIST_HEADERS & h:
        return "lists"
    if _INBOX_HEADERS & h and "email" in h:
        return "inboxes"
    return "unknown"


# ---------------------------------------------------------------------------
# Per-format parsers
# ---------------------------------------------------------------------------

def _parse_csv_content(content: str) -> ParseResult:
    result = ParseResult()
    try:
        sample = content[:2048]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except Exception:
            dialect = csv.excel

        reader = csv.DictReader(io.StringIO(content), dialect=dialect)
        headers = reader.fieldnames or []
        csv_type = _detect_csv_type(headers)

        rows = list(reader)
        if not rows:
            result.warnings.append("File contains no data rows")
            return result

        if csv_type == "ses_accounts":
            for i, row in enumerate(rows, 2):
                parsed = _parse_resend_row(row, i, result.errors)
                if parsed:
                    result.ses_accounts.append(parsed)

        elif csv_type == "smtp_accounts":
            for i, row in enumerate(rows, 2):
                parsed = _parse_smtp_row(row, i, result.errors)
                if parsed:
                    result.smtp_accounts.append(parsed)

        elif csv_type == "inboxes":
            for i, row in enumerate(rows, 2):
                parsed = _parse_inbox_row(row, i, result.errors)
                if parsed:
                    result.inboxes.append(parsed)

        elif csv_type == "lists":
            # list_name, list_description, email, first_name, last_name
            lists_map: Dict[str, ParsedList] = {}
            for i, row in enumerate(rows, 2):
                r = _normalize_headers(row)
                list_name = _clean(r.get("list_name") or "")
                if not list_name:
                    result.errors.append(f"Lists row {i}: missing list_name")
                    continue
                if list_name not in lists_map:
                    lists_map[list_name] = ParsedList(
                        list_name=list_name,
                        list_description=_clean(r.get("list_description") or ""),
                    )
                email = _clean(r.get("email") or "")
                if email:
                    if not _valid_email(email):
                        result.errors.append(f"Lists row {i}: invalid email '{email}'")
                        continue
                    rec = ParsedRecipient(
                        email=email,
                        first_name=_clean(r.get("first_name") or ""),
                        last_name=_clean(r.get("last_name") or ""),
                    )
                    lists_map[list_name].recipients.append(rec)
            result.lists = list(lists_map.values())

        else:
            result.errors.append(
                "Could not identify file type from headers. "
                "Expected columns for Resend (account_name, from_email_prefix, from_name), "
                "SMTP (account_name, from_email, host, username, password, encryption), "
                "Inboxes (email, account_name), or "
                "Lists (list_name, email)."
            )
    except Exception as exc:
        result.errors.append(f"CSV parse error: {exc}")

    return result


def _parse_xlsx_content(content: bytes) -> ParseResult:
    result = ParseResult()
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            sn = sheet_name.strip().lower().replace(" ", "_").replace("-", "_")

            rows_iter = ws.iter_rows(values_only=True)
            header_row = next(rows_iter, None)
            if header_row is None:
                continue
            headers = [str(h).strip() if h is not None else "" for h in header_row]

            if sn in ("resend_accounts", "resend", "ses_accounts", "ses"):
                for i, row in enumerate(rows_iter, 2):
                    row_dict = dict(zip(headers, row))
                    parsed = _parse_resend_row(row_dict, i, result.errors)
                    if parsed:
                        result.ses_accounts.append(parsed)

            elif sn in ("smtp_accounts", "smtp"):
                for i, row in enumerate(rows_iter, 2):
                    row_dict = dict(zip(headers, row))
                    parsed = _parse_smtp_row(row_dict, i, result.errors)
                    if parsed:
                        result.smtp_accounts.append(parsed)

            elif sn in ("inboxes", "inbox"):
                for i, row in enumerate(rows_iter, 2):
                    row_dict = dict(zip(headers, row))
                    parsed = _parse_inbox_row(row_dict, i, result.errors)
                    if parsed:
                        result.inboxes.append(parsed)

            elif sn in ("lists", "list"):
                # lists sheet: list_key, name, description
                lists_map: Dict[str, ParsedList] = {}
                for i, row in enumerate(rows_iter, 2):
                    row_dict = _normalize_headers(dict(zip(headers, row)))
                    list_name = _clean(row_dict.get("name") or row_dict.get("list_name") or "")
                    list_key = _clean(row_dict.get("list_key") or list_name)
                    if not list_name:
                        continue
                    if list_key not in lists_map:
                        lists_map[list_key] = ParsedList(
                            list_name=list_name,
                            list_description=_clean(row_dict.get("description") or row_dict.get("list_description") or ""),
                        )
                result.lists.extend(lists_map.values())

            elif sn in ("recipients", "recipient"):
                # recipients sheet: list_key, email, first_name, last_name
                # Need to match recipients to their lists
                pending_recipients: List[Tuple[str, ParsedRecipient]] = []
                for i, row in enumerate(rows_iter, 2):
                    row_dict = _normalize_headers(dict(zip(headers, row)))
                    list_key = _clean(row_dict.get("list_key") or row_dict.get("list_name") or "")
                    email = _clean(row_dict.get("email") or "")
                    if not email or not list_key:
                        continue
                    if not _valid_email(email):
                        result.errors.append(f"Recipients sheet row {i}: invalid email '{email}'")
                        continue
                    rec = ParsedRecipient(
                        email=email,
                        first_name=_clean(row_dict.get("first_name") or ""),
                        last_name=_clean(row_dict.get("last_name") or ""),
                    )
                    pending_recipients.append((list_key, rec))

                # Attach recipients to matching parsed lists
                lists_by_key: Dict[str, ParsedList] = {}
                for lst in result.lists:
                    # Key is the list_name (or list_key if different — use name as fallback)
                    lists_by_key[lst.list_name] = lst

                for list_key, rec in pending_recipients:
                    if list_key in lists_by_key:
                        lists_by_key[list_key].recipients.append(rec)
                    else:
                        # Try to match by creating a new list entry
                        new_list = ParsedList(list_name=list_key)
                        new_list.recipients.append(rec)
                        lists_by_key[list_key] = new_list
                        result.lists.append(new_list)
                        result.warnings.append(
                            f"Recipients sheet: list_key '{list_key}' not found in lists sheet — created automatically"
                        )

            else:
                # Unknown sheet — try auto-detection
                all_rows = list(rows_iter)
                if not all_rows:
                    continue
                detected = _detect_csv_type(headers)
                result.warnings.append(
                    f"Sheet '{sheet_name}' was not a recognized sheet name — "
                    f"detected as '{detected}' by headers"
                )
                if detected == "ses_accounts":
                    for i, row in enumerate(all_rows, 2):
                        row_dict = dict(zip(headers, row))
                        parsed = _parse_resend_row(row_dict, i, result.errors)
                        if parsed:
                            result.ses_accounts.append(parsed)
                elif detected == "smtp_accounts":
                    for i, row in enumerate(all_rows, 2):
                        row_dict = dict(zip(headers, row))
                        parsed = _parse_smtp_row(row_dict, i, result.errors)
                        if parsed:
                            result.smtp_accounts.append(parsed)
                elif detected == "inboxes":
                    for i, row in enumerate(all_rows, 2):
                        row_dict = dict(zip(headers, row))
                        parsed = _parse_inbox_row(row_dict, i, result.errors)
                        if parsed:
                            result.inboxes.append(parsed)

    except Exception as exc:
        result.errors.append(f"Excel parse error: {exc}")

    return result


def _parse_list_text(text: str) -> ParseResult:
    """
    Parse structured text (TXT or PDF extracted text) for lists.
    Format:
        List Name: <name>
        Description: <optional>

        email, first_name, last_name
        email
        ...

        List Name: <next>
        ...
    """
    result = ParseResult()
    current_list: Optional[ParsedList] = None
    blank_after_header = False

    for raw_line in text.splitlines():
        line = raw_line.strip()

        # New list header
        if line.lower().startswith("list name:"):
            current_list = ParsedList(
                list_name=line[len("list name:"):].strip()
            )
            result.lists.append(current_list)
            blank_after_header = False
            continue

        # Description line
        if line.lower().startswith("description:") and current_list:
            current_list.list_description = line[len("description:"):].strip()
            continue

        # Blank line — separator between header and recipients or between lists
        if not line:
            blank_after_header = True
            continue

        # Recipient line (only if we have an active list and already seen blank)
        if current_list and blank_after_header:
            rec = _parse_recipient_line(line)
            if rec:
                current_list.recipients.append(rec)
            else:
                result.warnings.append(f"Skipped unrecognized line: '{line[:60]}'")

    if not result.lists:
        result.errors.append(
            "No lists found. Make sure each list starts with 'List Name: <name>' on its own line."
        )

    return result


def _extract_pdf_text(content: bytes) -> str:
    """Extract plain text from a PDF using pdfminer.six."""
    from pdfminer.high_level import extract_text_to_fp
    from pdfminer.layout import LAParams

    output = io.StringIO()
    extract_text_to_fp(io.BytesIO(content), output, laparams=LAParams())
    return output.getvalue()


def _parse_json_content(text: str) -> ParseResult:
    """Parse a contact-audit JSON export into Resend account candidates.

    Accepts either a top-level list of contact objects, or an object with a
    "files" key holding that list (other top-level keys, e.g. audit metadata,
    are ignored). Each contact becomes one Resend account: account_name=company,
    from_email_prefix=<local part of contact_email>, from_name=contact_name.
    """
    result = ParseResult()
    try:
        data = json.loads(text)
    except Exception as exc:
        result.errors.append(f"Invalid JSON: {exc}")
        return result

    if isinstance(data, list):
        records = data
    elif isinstance(data, dict) and isinstance(data.get("files"), list):
        records = data["files"]
    else:
        result.errors.append(
            "Could not identify contact records in this JSON file. Expected a top-level "
            "list of contacts, or an object with a \"files\" list, where each entry has a "
            "company name and a contact email."
        )
        return result

    if not records:
        result.warnings.append("File contains no contact records")
        return result

    for i, entry in enumerate(records, 1):
        if not isinstance(entry, dict):
            result.errors.append(f"Entry {i}: expected an object, got {type(entry).__name__}")
            continue

        e = _normalize_headers(entry)
        company = _clean(e.get("company") or e.get("company_name") or e.get("account_name") or "")
        contact_name = _clean(e.get("contact_name") or e.get("name") or "")
        contact_email = _clean(e.get("contact_email") or e.get("email") or "")

        if not company:
            result.errors.append(f"Entry {i}: missing company name")
            continue
        if "@" not in contact_email or not contact_email.split("@", 1)[0]:
            result.errors.append(f"Entry {i} ({company}): missing or invalid contact email")
            continue

        row = {
            "account_name": company,
            "from_email_prefix": contact_email.split("@", 1)[0],
            "from_name": contact_name or company,
        }
        parsed = _parse_resend_row(row, i, result.errors)
        if parsed:
            result.ses_accounts.append(parsed)

    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_import_file(filename: str, content: bytes) -> ParseResult:
    """
    Parse any supported import file and return a ParseResult.
    Caller should check result.is_valid and result.errors before committing.
    """
    ext = os.path.splitext(filename.lower())[1]

    if ext == ".xlsx":
        return _parse_xlsx_content(content)

    if ext in (".txt",):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        return _parse_list_text(text)

    if ext == ".pdf":
        try:
            text = _extract_pdf_text(content)
        except Exception as exc:
            result = ParseResult()
            result.errors.append(f"Could not extract text from PDF: {exc}")
            return result
        return _parse_list_text(text)

    if ext == ".csv":
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        return _parse_csv_content(text)

    if ext == ".json":
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        return _parse_json_content(text)

    result = ParseResult()
    result.errors.append(
        f"Unsupported file type '{ext}'. Accepted formats: .csv, .xlsx, .txt, .pdf, .json"
    )
    return result
