"""Tests de formateo local_datetime (UTC en BD → hora de visualización)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from app.config import get_settings
from app.core.datetime_display import display_today, local_datetime, resolve_display_timezone


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-key-for-unit-tests")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://saas:saas@localhost:5432/saas",  # pragma: allowlist secret
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("APP_DISPLAY_TIMEZONE", "Europe/Madrid")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_local_datetime_converts_utc_to_madrid_summer() -> None:
    dt = datetime(2026, 5, 20, 14, 0, tzinfo=UTC)
    assert local_datetime(dt) == "20/05/2026 16:00"


def test_local_datetime_converts_utc_to_madrid_winter() -> None:
    dt = datetime(2026, 1, 15, 14, 0, tzinfo=UTC)
    assert local_datetime(dt) == "15/01/2026 15:00"


def test_local_datetime_none_returns_em_dash() -> None:
    assert local_datetime(None) == "—"


def test_local_datetime_naive_assumes_utc() -> None:
    dt = datetime(2026, 5, 20, 14, 0)
    assert local_datetime(dt) == "20/05/2026 16:00"


def test_local_datetime_custom_format() -> None:
    dt = datetime(2026, 5, 20, 14, 0, tzinfo=UTC)
    assert local_datetime(dt, "%d/%m/%Y") == "20/05/2026"


def test_local_datetime_tenant_timezone_override() -> None:
    dt = datetime(2026, 5, 20, 14, 0, tzinfo=UTC)
    assert local_datetime(dt, timezone="Atlantic/Canary") == "20/05/2026 15:00"


def test_resolve_display_timezone_invalid_falls_back_to_config() -> None:
    tz = resolve_display_timezone("Not/A_Timezone")
    assert str(tz) == "Europe/Madrid"


def test_display_today_returns_date_instance() -> None:
    assert isinstance(display_today(), date)
