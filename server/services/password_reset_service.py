"""Shared, email-verified password-reset challenges."""

import hashlib
import hmac
import logging
import os
import secrets
from dataclasses import dataclass
from typing import Optional

from redis.exceptions import RedisError

from services.redis_client import get_redis_client
from services.resend_service import get_resend_service

logger = logging.getLogger(__name__)

CHALLENGE_TTL_SECONDS = 10 * 60
RATE_LIMIT_WINDOW_SECONDS = 15 * 60
MAX_EMAIL_REQUESTS_PER_WINDOW = 5
MAX_CLIENT_REQUESTS_PER_WINDOW = 20
MAX_CODE_ATTEMPTS = 3
_KEY_PREFIX = "auth:password-reset"

_RATE_LIMIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""

_VERIFY_SCRIPT = """
local code_digest = redis.call('HGET', KEYS[1], 'code_digest')
if not code_digest then
    return {'missing', '0'}
end
local attempts = redis.call('HINCRBY', KEYS[1], 'attempts', 1)
if attempts > tonumber(ARGV[3]) then
    redis.call('DEL', KEYS[1])
    return {'too_many', '0'}
end
if code_digest ~= ARGV[1] then
    return {'wrong', tostring(tonumber(ARGV[3]) - attempts)}
end
local email_digest = redis.call('HGET', KEYS[1], 'email_digest')
if email_digest ~= ARGV[2] then
    redis.call('DEL', KEYS[1])
    return {'email_mismatch', '0'}
end
redis.call('DEL', KEYS[1])
return {'ok', '0'}
"""


class PasswordResetUnavailable(RuntimeError):
    """Raised when secure reset state cannot be stored or verified."""


class PasswordResetRateLimited(RuntimeError):
    """Raised when a client requests too many reset challenges."""


@dataclass(frozen=True)
class PasswordResetChallenge:
    challenge_id: str
    code: str


def _secret_key() -> bytes:
    secret = os.getenv("SECRET_KEY", "")
    if not secret:
        raise PasswordResetUnavailable("Password reset is not configured")
    return secret.encode("utf-8")


def _normalize_email(email: str) -> str:
    return email.strip().casefold()


def _email_digest(email: str) -> str:
    return hashlib.sha256(_normalize_email(email).encode("utf-8")).hexdigest()


def _code_digest(challenge_id: str, email: str, code: str) -> str:
    payload = f"{challenge_id}:{_normalize_email(email)}:{code}".encode("utf-8")
    return hmac.new(_secret_key(), payload, hashlib.sha256).hexdigest()


def _challenge_key(challenge_id: str) -> str:
    return f"{_KEY_PREFIX}:challenge:{challenge_id}"


def _increment_rate_limit(key: str, limit: int) -> bool:
    count = get_redis_client().eval(
        _RATE_LIMIT_SCRIPT,
        1,
        key,
        RATE_LIMIT_WINDOW_SECONDS,
    )
    return int(count) <= limit


def issue_password_reset_challenge(email: str, client_id: str) -> PasswordResetChallenge:
    """Create a short-lived one-time code in shared Redis state."""
    normalized_email = _normalize_email(email)
    email_hash = _email_digest(normalized_email)
    client_hash = hashlib.sha256((client_id or "unknown").encode("utf-8")).hexdigest()

    try:
        email_allowed = _increment_rate_limit(
            f"{_KEY_PREFIX}:rate:email:{email_hash}",
            MAX_EMAIL_REQUESTS_PER_WINDOW,
        )
        client_allowed = _increment_rate_limit(
            f"{_KEY_PREFIX}:rate:client:{client_hash}",
            MAX_CLIENT_REQUESTS_PER_WINDOW,
        )
        if not email_allowed or not client_allowed:
            raise PasswordResetRateLimited("Too many password reset requests")

        challenge_id = secrets.token_urlsafe(32)
        code = f"{secrets.randbelow(1_000_000):06d}"
        client = get_redis_client()
        with client.pipeline(transaction=True) as pipeline:
            pipeline.hset(
                _challenge_key(challenge_id),
                mapping={
                    "code_digest": _code_digest(challenge_id, normalized_email, code),
                    "email_digest": email_hash,
                    "attempts": 0,
                },
            )
            pipeline.expire(_challenge_key(challenge_id), CHALLENGE_TTL_SECONDS)
            pipeline.execute()
    except PasswordResetRateLimited:
        raise
    except (RedisError, OSError) as exc:
        logger.error("Password reset state is unavailable: %s", type(exc).__name__)
        raise PasswordResetUnavailable("Password reset is temporarily unavailable") from exc

    return PasswordResetChallenge(challenge_id=challenge_id, code=code)


def verify_and_consume_password_reset_challenge(
    challenge_id: str,
    email: str,
    code: str,
) -> tuple[str, int]:
    """Atomically verify, attempt-limit, and consume a reset challenge."""
    try:
        result = get_redis_client().eval(
            _VERIFY_SCRIPT,
            1,
            _challenge_key(challenge_id),
            _code_digest(challenge_id, email, code.strip()),
            _email_digest(email),
            MAX_CODE_ATTEMPTS,
        )
    except (RedisError, OSError) as exc:
        logger.error("Password reset verification is unavailable: %s", type(exc).__name__)
        raise PasswordResetUnavailable("Password reset is temporarily unavailable") from exc

    status = str(result[0])
    remaining = int(result[1])
    return status, remaining


def send_password_reset_code(email: str, code: str) -> None:
    """Send a reset code without exposing provider failures to public callers."""
    sender = get_resend_service()
    from_email = os.getenv("AUTH_FROM_EMAIL", "").strip()
    from_name = os.getenv("AUTH_FROM_NAME", "FLEETCTRL-X").strip()
    if sender is None or not from_email:
        logger.error("Password reset email provider is not configured")
        return

    result = sender.send_email_sync(
        to_email=_normalize_email(email),
        to_name=None,
        from_email=from_email,
        from_name=from_name,
        subject="Your password reset verification code",
        text_content=(
            f"Your verification code is {code}. "
            f"It expires in {CHALLENGE_TTL_SECONDS // 60} minutes. "
            "If you did not request this reset, ignore this message."
        ),
        html_content=(
            "<p>Your password reset verification code is:</p>"
            f"<p style=\"font-size:24px;font-weight:700;letter-spacing:4px\">{code}</p>"
            f"<p>It expires in {CHALLENGE_TTL_SECONDS // 60} minutes. "
            "If you did not request this reset, ignore this message.</p>"
        ),
    )
    if not result.get("success"):
        logger.error("Password reset email delivery failed")
