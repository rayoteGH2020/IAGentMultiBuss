"""Tests de validación de citas contra horario del centro."""

from datetime import date, datetime, time
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from app.core.business_hours_validation import (
    build_grid_times_by_weekday,
    center_grid_start_times,
    center_hour_ranges_for_weekday,
    validate_appointment_start_on_grid,
    validate_appointment_within_center_hours,
)
from app.core.errors import ValidationError
from app.schemas.scheduling import BusinessHourRead

TZ = ZoneInfo("Europe/Madrid")


def _hour(
    weekday: int,
    sort_order: int,
    opens: str | None,
    closes: str | None,
) -> BusinessHourRead:
    return BusinessHourRead(
        id=uuid4(),
        weekday=weekday,
        sort_order=sort_order,
        opens_at=time.fromisoformat(opens) if opens else None,
        closes_at=time.fromisoformat(closes) if closes else None,
    )


def _center_hours() -> list[BusinessHourRead]:
    return [
        _hour(0, 0, "09:00", "14:00"),
        _hour(0, 1, "16:00", "21:00"),
    ]


def test_center_hour_ranges_for_weekday_filters_closed_slots() -> None:
    hours = [
        _hour(0, 0, "09:00", "14:00"),
        _hour(0, 1, None, None),
        _hour(1, 0, "10:00", "18:00"),
    ]
    assert center_hour_ranges_for_weekday(hours, 0) == [(time(9, 0), time(14, 0))]
    assert center_hour_ranges_for_weekday(hours, 1) == [(time(10, 0), time(18, 0))]


def test_validate_accepts_appointment_in_morning_slot() -> None:
    start = datetime(2026, 7, 13, 10, 0, tzinfo=TZ)
    end = datetime(2026, 7, 13, 10, 30, tzinfo=TZ)
    validate_appointment_within_center_hours(
        start,
        end,
        _center_hours(),
        timezone_name="Europe/Madrid",
    )


def test_validate_accepts_appointment_in_afternoon_slot() -> None:
    start = datetime(2026, 7, 13, 17, 0, tzinfo=TZ)
    end = datetime(2026, 7, 13, 18, 0, tzinfo=TZ)
    validate_appointment_within_center_hours(
        start,
        end,
        _center_hours(),
        timezone_name="Europe/Madrid",
    )


def test_validate_rejects_appointment_during_lunch_break() -> None:
    start = datetime(2026, 7, 13, 13, 30, tzinfo=TZ)
    end = datetime(2026, 7, 13, 14, 30, tzinfo=TZ)
    with pytest.raises(ValidationError, match="business hours"):
        validate_appointment_within_center_hours(
            start,
            end,
            _center_hours(),
            timezone_name="Europe/Madrid",
        )


def test_validate_rejects_closed_weekday() -> None:
    start = datetime(2026, 7, 12, 10, 0, tzinfo=TZ)
    end = datetime(2026, 7, 12, 10, 30, tzinfo=TZ)
    with pytest.raises(ValidationError, match="closed on this weekday"):
        validate_appointment_within_center_hours(
            start,
            end,
            _center_hours(),
            timezone_name="Europe/Madrid",
        )


def test_validate_rejects_closed_exception_date() -> None:
    start = datetime(2026, 7, 13, 10, 0, tzinfo=TZ)
    end = datetime(2026, 7, 13, 10, 30, tzinfo=TZ)
    with pytest.raises(ValidationError, match="closed on this date"):
        validate_appointment_within_center_hours(
            start,
            end,
            _center_hours(),
            timezone_name="Europe/Madrid",
            closed_dates={date(2026, 7, 13)},
        )


def test_validate_rejects_appointment_spanning_days() -> None:
    start = datetime(2026, 7, 13, 20, 30, tzinfo=TZ)
    end = datetime(2026, 7, 14, 0, 30, tzinfo=TZ)
    with pytest.raises(ValidationError, match="same calendar day"):
        validate_appointment_within_center_hours(
            start,
            end,
            _center_hours(),
            timezone_name="Europe/Madrid",
        )


def test_validate_rejects_start_not_on_grid() -> None:
    start = datetime(2026, 7, 13, 14, 0, tzinfo=TZ)
    with pytest.raises(ValidationError, match="grid slot"):
        validate_appointment_start_on_grid(
            date(2026, 7, 13),
            start.time().replace(tzinfo=None),
            _center_hours(),
            15,
        )


def test_build_grid_times_by_weekday_matches_morning_slots() -> None:
    grid = build_grid_times_by_weekday(_center_hours(), 15)
    assert grid[0][:3] == ["09:00", "09:15", "09:30"]
    assert "13:45" in grid[0]
    assert "14:00" not in grid[0]
    assert grid[6] == []


def test_center_grid_start_times_excludes_close_boundary() -> None:
    times = center_grid_start_times(_center_hours(), 0, 15)
    assert time(9, 0) in times
    assert time(13, 45) in times
    assert time(14, 0) not in times
