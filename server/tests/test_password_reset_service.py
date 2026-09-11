import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import redis

from services import password_reset_service


TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
if urlsplit(TEST_REDIS_URL).path.rstrip("/") != "/15":
    raise RuntimeError("TEST_REDIS_URL must select isolated Redis DB 15")


def test_password_reset_code_is_attempt_limited_and_single_use(monkeypatch):
    client = redis.from_url(TEST_REDIS_URL, decode_responses=True)
    monkeypatch.setenv("SECRET_KEY", "test-password-reset-secret")
    monkeypatch.setattr(password_reset_service, "get_redis_client", lambda: client)

    nonce = uuid4().hex
    email = f"reset-{nonce}@example.com"
    challenge = password_reset_service.issue_password_reset_challenge(
        email,
        f"test-client-{nonce}",
    )

    try:
        status, remaining = password_reset_service.verify_and_consume_password_reset_challenge(
            challenge.challenge_id,
            email,
            "000000" if challenge.code != "000000" else "999999",
        )
        assert (status, remaining) == ("wrong", 2)

        status, remaining = password_reset_service.verify_and_consume_password_reset_challenge(
            challenge.challenge_id,
            email,
            challenge.code,
        )
        assert (status, remaining) == ("ok", 0)

        status, _ = password_reset_service.verify_and_consume_password_reset_challenge(
            challenge.challenge_id,
            email,
            challenge.code,
        )
        assert status == "missing"
    finally:
        client.delete(password_reset_service._challenge_key(challenge.challenge_id))


def test_password_reset_challenge_is_bound_to_email(monkeypatch):
    client = redis.from_url(TEST_REDIS_URL, decode_responses=True)
    monkeypatch.setenv("SECRET_KEY", "test-password-reset-secret")
    monkeypatch.setattr(password_reset_service, "get_redis_client", lambda: client)

    nonce = uuid4().hex
    email = f"owner-{nonce}@example.com"
    challenge = password_reset_service.issue_password_reset_challenge(
        email,
        f"test-client-{nonce}",
    )

    try:
        status, _ = password_reset_service.verify_and_consume_password_reset_challenge(
            challenge.challenge_id,
            f"attacker-{nonce}@example.com",
            challenge.code,
        )
        assert status == "wrong"
    finally:
        client.delete(password_reset_service._challenge_key(challenge.challenge_id))


def test_password_reset_requires_application_secret(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with pytest.raises(password_reset_service.PasswordResetUnavailable):
        password_reset_service._code_digest("challenge", "user@example.com", "123456")
