"""Tests de parseo de formularios de horarios."""

from datetime import time

import pytest
from app.core.errors import ValidationError
from app.core.scheduling_form_parsers import (
    parse_business_hours_form,
    parse_optional_time,
)
from app.core.scheduling_ui import WEEKDAY_LABELS


def test_parse_optional_time_accepts_hh_mm() -> None:
    assert parse_optional_time("09:30") == time(9, 30)


def test_parse_optional_time_empty_returns_none() -> None:
    assert parse_optional_time("") is None
    assert parse_optional_time(None) is None


def test_parse_optional_time_rejects_invalid_format() -> None:
    with pytest.raises(ValidationError, match="HH:MM"):
        parse_optional_time("9")


def test_parse_business_hours_form_accepts_complete_morning_slot() -> None:
    payload = parse_business_hours_form(
        weekday_forms=[(0, 0, "09:00", "14:00"), (0, 1, None, None)],
        weekday_labels=WEEKDAY_LABELS,
    )
    assert payload.slots[0].opens_at == time(9, 0)
    assert payload.slots[0].closes_at == time(14, 0)
    assert payload.slots[1].opens_at is None


def test_parse_business_hours_form_rejects_partial_morning_slot() -> None:
    with pytest.raises(ValidationError, match="mañana"):
        parse_business_hours_form(
            weekday_forms=[(0, 0, "09:00", None)],
            weekday_labels=WEEKDAY_LABELS,
        )


def test_parse_business_hours_form_rejects_partial_afternoon_slot() -> None:
    with pytest.raises(ValidationError, match="tarde"):
        parse_business_hours_form(
            weekday_forms=[(1, 1, None, "21:00")],
            weekday_labels=WEEKDAY_LABELS,
        )


def test_parse_business_hours_form_rejects_opens_after_closes() -> None:
    with pytest.raises(ValidationError, match="inicio debe ser anterior"):
        parse_business_hours_form(
            weekday_forms=[(0, 0, "14:00", "09:00")],
            weekday_labels=WEEKDAY_LABELS,
        )
