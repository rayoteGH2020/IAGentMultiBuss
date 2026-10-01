"""ACTIVITY_LOG_RETENTION_DAYS: 0 prohibido en producción, mínimo 7 (D029)."""

from __future__ import annotations

import pytest
from app.config import Settings
from pydantic import ValidationError

_BASE = {
    "app_secret_key": "x" * 32,
    "database_url": "postgresql+asyncpg://u:p@localhost/db",  # pragma: allowlist secret
    "redis_url": "redis://localhost:6379/0",
    # Neutraliza lo que Infisical dev pueda inyectar y otros validadores rechacen
    # fuera de desarrollo.
    "clerk_jwks_url": "",
    "webhook_allow_unsigned": False,
    "langfuse_capture_content": False,
}


def _settings(**overrides: object) -> Settings:
    return Settings(**{**_BASE, **overrides})


def test_zero_retention_refuses_to_start_in_production() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _settings(app_env="production", activity_log_retention_days=0)

    message = str(excinfo.value)
    assert "ACTIVITY_LOG_RETENTION_DAYS=0" in message
    assert "APP_ENV=production" in message
    assert "RGPD" in message
    assert "recomendado: 90" in message


@pytest.mark.parametrize("env", ["development", "staging"])
def test_zero_retention_allowed_outside_production(env: str) -> None:
    assert _settings(app_env=env, activity_log_retention_days=0).activity_log_retention_days == 0


@pytest.mark.parametrize("days", [1, 6, -5])
def test_retention_below_minimum_is_rejected(days: int) -> None:
    with pytest.raises(ValidationError, match="7 o más"):
        _settings(activity_log_retention_days=days)


def test_default_and_minimum_retention_are_valid_in_production() -> None:
    assert _settings(app_env="production").activity_log_retention_days == 90
    minimum = _settings(app_env="production", activity_log_retention_days=7)
    assert minimum.activity_log_retention_days == 7
