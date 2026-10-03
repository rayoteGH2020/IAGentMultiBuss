"""Settings: listas CSV/JSON desde Infisical (p. ej. SECURITY_ALLOWED_HOSTS)."""

from __future__ import annotations

import os

import pytest
from app.config import Settings, _parse_comma_or_json_str_list, get_settings


def test_parse_comma_separated_hosts() -> None:
    assert _parse_comma_or_json_str_list("localhost,127.0.0.1, 192.168.1.42") == [
        "localhost",
        "127.0.0.1",
        "192.168.1.42",
    ]


def test_parse_json_list() -> None:
    assert _parse_comma_or_json_str_list('["localhost", "127.0.0.1"]') == [
        "localhost",
        "127.0.0.1",
    ]


def test_parse_already_list() -> None:
    assert _parse_comma_or_json_str_list(["a", " b "]) == ["a", "b"]


def test_settings_accepts_csv_security_allowed_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv(
        "SECURITY_ALLOWED_HOSTS",
        "localhost,127.0.0.1,192.168.1.42,192.168.1.130",
    )
    get_settings.cache_clear()
    try:
        settings = Settings()
        assert settings.security_allowed_hosts == [
            "localhost",
            "127.0.0.1",
            "192.168.1.42",
            "192.168.1.130",
        ]
    finally:
        get_settings.cache_clear()


def test_settings_loads_with_infisical_style_hosts_if_present() -> None:
    """Smoke: si Infisical inyectó CSV, Settings() no debe romper."""
    raw = os.environ.get("SECURITY_ALLOWED_HOSTS")
    if raw is None or raw.strip().startswith("["):
        pytest.skip("SECURITY_ALLOWED_HOSTS no está en CSV en este entorno")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert isinstance(settings.security_allowed_hosts, list)
        assert len(settings.security_allowed_hosts) >= 1
    finally:
        get_settings.cache_clear()
