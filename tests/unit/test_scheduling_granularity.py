"""Tests de granularidad de tramos de citas."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from app.core.errors import ValidationError
from app.core.scheduling_granularity import (
    DEFAULT_SLOT_GRANULARITY_MINUTES,
    datetime_local_input_step_seconds,
    slot_minute_options,
    validate_datetime_granularity,
)

TZ = ZoneInfo("Europe/Madrid")


def test_default_slot_granularity_is_fifteen() -> None:
    assert DEFAULT_SLOT_GRANULARITY_MINUTES == 15


@pytest.mark.parametrize(
    ("granularity", "expected"),
    [
        (15, ["00", "15", "30", "45"]),
        (30, ["00", "30"]),
        (20, ["00", "20", "40"]),
        (5, ["00", "05", "10", "15", "20", "25", "30", "35", "40", "45", "50", "55"]),
    ],
)
def test_slot_minute_options(granularity: int, expected: list[str]) -> None:
    assert slot_minute_options(granularity) == expected


def test_validate_datetime_granularity_accepts_aligned_time() -> None:
    dt = datetime(2026, 7, 15, 10, 30, tzinfo=TZ)
    validate_datetime_granularity(dt, 30)


def test_validate_datetime_granularity_rejects_misaligned_time() -> None:
    dt = datetime(2026, 7, 15, 10, 25, tzinfo=TZ)
    with pytest.raises(ValidationError, match="30-minute"):
        validate_datetime_granularity(dt, 30)


def test_datetime_local_input_step_seconds() -> None:
    assert datetime_local_input_step_seconds(15) == 900
    assert datetime_local_input_step_seconds(30) == 1800
