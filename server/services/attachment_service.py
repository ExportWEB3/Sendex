"""Safe storage and resolution helpers for email attachments."""

from __future__ import annotations

import mimetypes
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

UPLOAD_ROOT = Path(
    os.environ.get(
        "UPLOADS_DIR",
        Path(__file__).resolve().parent.parent / "uploads",
    )
).resolve()

ALLOWED_ATTACHMENT_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
    ".mp4", ".mov", ".avi", ".webm",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".csv", ".zip", ".rar",
}
MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024
MAX_ATTACHMENTS_PER_MESSAGE = 20
_STORED_NAME_RE = re.compile(r"^[0-9a-f]{32}\.[a-z0-9]{1,8}$")
_PUBLIC_FIELDS = ("filename", "stored_name", "content_type", "size")


def _safe_display_name(filename: str, fallback: str) -> str:
    normalized = str(filename or "").replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1]
    cleaned = "".join(character for character in basename if character >= " " and character != "\x7f")
    return (cleaned.strip() or fallback)[:255]


def resolve_attachment_path(stored_name: str) -> Optional[Path]:
    """Resolve a generated attachment name without accepting arbitrary paths."""
    normalized = str(stored_name or "").lower()
    if not _STORED_NAME_RE.fullmatch(normalized):
        return None
    path = (UPLOAD_ROOT / normalized).resolve()
    if path.parent != UPLOAD_ROOT or not path.is_file():
        return None
    return path


def store_attachment(content: bytes, original_filename: str) -> Dict[str, Any]:
    """Persist a validated upload and return server-side metadata."""
    extension = Path(original_filename or "").suffix.lower()
    if extension not in ALLOWED_ATTACHMENT_EXTENSIONS:
        raise ValueError(f"File type '{extension}' not allowed")
    if len(content) > MAX_ATTACHMENT_SIZE:
        raise ValueError(f"File too large (max {MAX_ATTACHMENT_SIZE // 1024 // 1024}MB)")

    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{extension}"
    path = UPLOAD_ROOT / stored_name
    path.write_bytes(content)
    path.chmod(0o600)

    content_type = mimetypes.guess_type(stored_name)[0] or "application/octet-stream"
    return {
        "filename": _safe_display_name(original_filename, stored_name),
        "stored_name": stored_name,
        "filepath": str(path),
        "content_type": content_type,
        "size": len(content),
    }


def normalize_attachment_metadata(attachments: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Build trusted metadata from stored names, ignoring client-supplied paths."""
    items = list(attachments or [])
    if len(items) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise ValueError(f"A maximum of {MAX_ATTACHMENTS_PER_MESSAGE} attachments is allowed")

    normalized: List[Dict[str, Any]] = []
    for item in items:
        stored_name = str(item.get("stored_name") or "").lower()
        path = resolve_attachment_path(stored_name)
        if path is None:
            raise ValueError("Attachment is missing or has an invalid stored name")
        normalized.append({
            "filename": _safe_display_name(str(item.get("filename") or ""), stored_name),
            "stored_name": stored_name,
            "filepath": str(path),
            "content_type": mimetypes.guess_type(stored_name)[0] or "application/octet-stream",
            "size": path.stat().st_size,
        })
    return normalized


def public_attachment_metadata(attachments: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Remove private filesystem paths from API response metadata."""
    return [
        {field: item.get(field) for field in _PUBLIC_FIELDS}
        for item in attachments or []
    ]
