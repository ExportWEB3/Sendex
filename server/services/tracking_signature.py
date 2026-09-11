"""HMAC signatures for public email tracking URLs."""

import hashlib
import hmac
import os
from typing import Optional


def _signing_key() -> bytes:
    secret = os.getenv("TRACKING_SECRET") or os.getenv("SECRET_KEY")
    return secret.encode("utf-8") if secret else b""


def sign_tracking_request(
    campaign_id: int,
    recipient_id: Optional[int] = None,
    destination_url: str = "",
) -> str:
    """Sign immutable tracking parameters without exposing the secret."""
    key = _signing_key()
    if not key:
        return ""
    payload = f"{campaign_id}:{recipient_id or ''}:{destination_url}".encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def verify_tracking_signature(
    signature: str,
    campaign_id: int,
    recipient_id: Optional[int] = None,
    destination_url: str = "",
) -> bool:
    """Validate a tracking signature and fail closed when no key is configured."""
    expected = sign_tracking_request(campaign_id, recipient_id, destination_url)
    return bool(expected and signature and hmac.compare_digest(expected, signature))
