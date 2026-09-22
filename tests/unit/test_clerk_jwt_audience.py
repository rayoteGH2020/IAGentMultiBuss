"""Validación de claims azp/aud en JWT de Clerk (Paso01 §3)."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import jwt
import pytest
from app.config import Settings
from app.core.errors import AuthError
from app.core.security import assert_clerk_jwt_authorized_party, verify_clerk_jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError


def _base_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "app_secret_key": "test-secret",  # pragma: allowlist secret
        "database_url": "postgresql+asyncpg://x@localhost/db",  # pragma: allowlist secret
        "redis_url": "redis://localhost:6379/0",
        "app_env": "development",
        # Anula CLERK_* inyectados por Infisical salvo que el test los fije.
        "clerk_jwks_url": "",
        "clerk_jwt_azp_allowlist": [],
        "clerk_jwt_audience_allowlist": [],
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _rsa_keypair() -> tuple[rsa.RSAPrivateKey, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_key, public_pem


def _sign_token(private_key: rsa.RSAPrivateKey, claims: dict[str, Any]) -> str:
    payload = {
        "sub": "user_test",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
        **claims,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def test_settings_require_azp_or_aud_in_production_with_jwks() -> None:
    with pytest.raises(ValidationError, match="CLERK_JWT_AZP_ALLOWLIST"):
        _base_settings(
            app_env="production",
            clerk_jwks_url="https://example.clerk.accounts.dev/.well-known/jwks.json",
        )


def test_settings_require_azp_or_aud_in_staging_with_jwks() -> None:
    with pytest.raises(ValidationError, match="CLERK_JWT_AZP_ALLOWLIST"):
        _base_settings(
            app_env="staging",
            clerk_jwks_url="https://example.clerk.accounts.dev/.well-known/jwks.json",
        )


def test_settings_production_ok_without_jwks() -> None:
    s = _base_settings(app_env="production")
    assert s.clerk_jwt_azp_allowlist == []


def test_settings_accept_azp_allowlist_in_production() -> None:
    s = _base_settings(
        app_env="production",
        clerk_jwks_url="https://example.clerk.accounts.dev/.well-known/jwks.json",
        clerk_jwt_azp_allowlist=["http://localhost:8000"],
    )
    assert s.clerk_jwt_azp_allowlist == ["http://localhost:8000"]


def test_assert_skips_when_allowlists_empty() -> None:
    settings = _base_settings()
    assert_clerk_jwt_authorized_party({"sub": "u1"}, settings=settings)


def test_assert_rejects_missing_azp_when_required() -> None:
    settings = _base_settings(clerk_jwt_azp_allowlist=["https://app.example"])
    with pytest.raises(AuthError, match="missing required azp"):
        assert_clerk_jwt_authorized_party({"sub": "u1"}, settings=settings)


def test_assert_rejects_wrong_azp() -> None:
    settings = _base_settings(clerk_jwt_azp_allowlist=["https://app.example"])
    with pytest.raises(AuthError, match="authorized party"):
        assert_clerk_jwt_authorized_party(
            {"azp": "https://other.example"},
            settings=settings,
        )


def test_assert_accepts_matching_azp() -> None:
    settings = _base_settings(clerk_jwt_azp_allowlist=["https://app.example"])
    assert_clerk_jwt_authorized_party(
        {"azp": "https://app.example"},
        settings=settings,
    )


def test_assert_rejects_wrong_aud() -> None:
    settings = _base_settings(clerk_jwt_audience_allowlist=["clerk-instance"])
    with pytest.raises(AuthError, match="audience"):
        assert_clerk_jwt_authorized_party({"aud": "other"}, settings=settings)


def test_assert_accepts_aud_list_intersection() -> None:
    settings = _base_settings(clerk_jwt_audience_allowlist=["expected"])
    assert_clerk_jwt_authorized_party(
        {"aud": ["other", "expected"]},
        settings=settings,
    )


def test_verify_clerk_jwt_rejects_invalid_azp(monkeypatch: pytest.MonkeyPatch) -> None:
    private_key, public_pem = _rsa_keypair()
    token = _sign_token(private_key, {"azp": "https://wrong.example"})

    signing_key = MagicMock()
    signing_key.key = public_pem
    jwks = MagicMock()
    jwks.get_signing_key_from_jwt.return_value = signing_key
    monkeypatch.setattr("app.core.security._get_jwks_client", lambda: jwks)
    monkeypatch.setattr(
        "app.core.security.get_settings",
        lambda: SimpleNamespace(
            clerk_jwt_azp_allowlist=["https://app.example"],
            clerk_jwt_audience_allowlist=[],
        ),
    )

    with pytest.raises(AuthError, match="authorized party"):
        verify_clerk_jwt(token)


def test_verify_clerk_jwt_accepts_valid_azp(monkeypatch: pytest.MonkeyPatch) -> None:
    private_key, public_pem = _rsa_keypair()
    token = _sign_token(private_key, {"azp": "https://app.example", "org_id": "org_1"})

    signing_key = MagicMock()
    signing_key.key = public_pem
    jwks = MagicMock()
    jwks.get_signing_key_from_jwt.return_value = signing_key
    monkeypatch.setattr("app.core.security._get_jwks_client", lambda: jwks)
    monkeypatch.setattr(
        "app.core.security.get_settings",
        lambda: SimpleNamespace(
            clerk_jwt_azp_allowlist=["https://app.example"],
            clerk_jwt_audience_allowlist=[],
        ),
    )

    claims = verify_clerk_jwt(token)
    assert claims["azp"] == "https://app.example"
    assert claims["org_id"] == "org_1"


def test_verify_clerk_jwt_rejects_missing_required_azp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key, public_pem = _rsa_keypair()
    token = _sign_token(private_key, {})

    signing_key = MagicMock()
    signing_key.key = public_pem
    jwks = MagicMock()
    jwks.get_signing_key_from_jwt.return_value = signing_key
    monkeypatch.setattr("app.core.security._get_jwks_client", lambda: jwks)
    monkeypatch.setattr(
        "app.core.security.get_settings",
        lambda: SimpleNamespace(
            clerk_jwt_azp_allowlist=["https://app.example"],
            clerk_jwt_audience_allowlist=[],
        ),
    )

    with pytest.raises(AuthError, match="missing required azp"):
        verify_clerk_jwt(token)
