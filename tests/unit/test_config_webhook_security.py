"""Settings de seguridad de webhooks externos (CDX punto 4)."""

from __future__ import annotations

import pytest
from app.config import Settings
from pydantic import ValidationError


def _base_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "app_secret_key": "test-secret",  # pragma: allowlist secret
        "database_url": "postgresql+asyncpg://x@localhost/db",  # pragma: allowlist secret
        "redis_url": "redis://localhost:6379/0",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_webhook_allow_unsigned_default_false() -> None:
    s = _base_settings()
    assert s.webhook_allow_unsigned is False
    assert s.allows_unsigned_webhooks is False


def test_allows_unsigned_webhooks_only_in_dev_with_flag() -> None:
    s = _base_settings(app_env="development", webhook_allow_unsigned=True)
    assert s.allows_unsigned_webhooks is True

    s_prod_flag = _base_settings(app_env="production", webhook_allow_unsigned=False)
    assert s_prod_flag.allows_unsigned_webhooks is False


def test_webhook_allow_unsigned_rejected_in_production() -> None:
    with pytest.raises(ValidationError, match="WEBHOOK_ALLOW_UNSIGNED"):
        _base_settings(app_env="production", webhook_allow_unsigned=True)


def test_webhook_allow_unsigned_rejected_in_staging() -> None:
    with pytest.raises(ValidationError, match="WEBHOOK_ALLOW_UNSIGNED"):
        _base_settings(app_env="staging", webhook_allow_unsigned=True)
