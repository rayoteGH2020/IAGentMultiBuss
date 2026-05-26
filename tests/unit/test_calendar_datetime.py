"""Tests de conversión de fechas para Google Calendar."""

from __future__ import annotations

from datetime import date

import pytest
from app.core.calendar_datetime import (
    calendar_event_date_chip,
    calendar_event_is_all_day,
    format_calendar_event_time,
    format_week_range_label,
    google_iso_to_local_input,
    local_input_to_google_iso,
    parse_week_start,
    shift_week_start,
    week_bounds_google_iso,
)
from app.core.datetime_display import display_today
from app.core.errors import ValidationError


def test_local_input_to_google_iso_includes_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_DISPLAY_TIMEZONE", "Europe/Madrid")
    from app.config import get_settings

    get_settings.cache_clear()
    iso = local_input_to_google_iso("2026-05-26T15:30")
    assert "2026-05-26T15:30" in iso
    assert "+" in iso or iso.endswith("Z")
    get_settings.cache_clear()


def test_format_calendar_event_time_all_day() -> None:
    assert format_calendar_event_time("2026-05-26") == "26/05/2026"
    assert format_calendar_event_time("2026-05-26", "%H:%M") == "Todo el día"


def test_calendar_event_date_chip() -> None:
    chip = calendar_event_date_chip("2026-05-26")
    assert chip == {"day": "26", "month": "MAY"}


def test_calendar_event_is_all_day() -> None:
    assert calendar_event_is_all_day("2026-05-26") is True
    assert calendar_event_is_all_day("2026-05-26T10:00:00Z") is False


def test_google_iso_to_local_input_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_DISPLAY_TIMEZONE", "UTC")
    from app.config import get_settings

    get_settings.cache_clear()
    local = google_iso_to_local_input("2026-05-26T10:00:00Z")
    assert local.startswith("2026-05-26T10:00")
    get_settings.cache_clear()


def test_local_input_empty_raises() -> None:
    with pytest.raises(ValidationError):
        local_input_to_google_iso("  ")


def test_parse_week_start_defaults_to_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_DISPLAY_TIMEZONE", "UTC")
    from app.config import get_settings

    get_settings.cache_clear()
    assert parse_week_start(None) == display_today()
    get_settings.cache_clear()


def test_shift_week_start() -> None:
    start = date(2026, 5, 26)
    assert shift_week_start(start, 1) == date(2026, 6, 2)
    assert shift_week_start(start, -1) == date(2026, 5, 19)


def test_format_week_range_label() -> None:
    assert format_week_range_label(date(2026, 5, 26)) == "26/05/2026 - 01/06/2026"


def test_week_bounds_google_iso(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_DISPLAY_TIMEZONE", "UTC")
    from app.config import get_settings

    get_settings.cache_clear()
    time_min, time_max = week_bounds_google_iso(date(2026, 5, 26))
    assert time_min.startswith("2026-05-26")
    assert time_max.startswith("2026-06-02")
    get_settings.cache_clear()
