"""Tests for signed CSRF token helpers."""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.config import get_settings
from app.core.csrf import CSRF_TOKEN_TTL_SECONDS, generate_csrf_token, validate_csrf_token


def test_csrf_token_validates_for_same_user_and_tenant() -> None:
    user_id = uuid4()
    tenant_id = uuid4()

    token = generate_csrf_token(user_id=user_id, tenant_id=tenant_id, now=100)

    assert validate_csrf_token(token, user_id=user_id, tenant_id=tenant_id, now=100)


def test_csrf_token_rejects_tampering() -> None:
    user_id = uuid4()
    tenant_id = uuid4()
    token = generate_csrf_token(user_id=user_id, tenant_id=tenant_id, now=100)

    tampered = f"{token}x"

    assert not validate_csrf_token(tampered, user_id=user_id, tenant_id=tenant_id, now=100)


def test_csrf_token_rejects_other_tenant() -> None:
    user_id = uuid4()
    token = generate_csrf_token(user_id=user_id, tenant_id=uuid4(), now=100)

    assert not validate_csrf_token(token, user_id=user_id, tenant_id=uuid4(), now=100)


def test_csrf_token_rejects_expired_token() -> None:
    user_id = uuid4()
    tenant_id = uuid4()
    token = generate_csrf_token(user_id=user_id, tenant_id=tenant_id, now=100)

    assert not validate_csrf_token(
        token,
        user_id=user_id,
        tenant_id=tenant_id,
        now=100 + CSRF_TOKEN_TTL_SECONDS + 1,
    )


def test_csrf_token_rejects_after_secret_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = uuid4()
    tenant_id = uuid4()
    token = generate_csrf_token(user_id=user_id, tenant_id=tenant_id, now=100)

    monkeypatch.setenv("APP_SECRET_KEY", "rotated-test-secret")
    get_settings.cache_clear()
    try:
        assert not validate_csrf_token(token, user_id=user_id, tenant_id=tenant_id, now=100)
    finally:
        get_settings.cache_clear()
