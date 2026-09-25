"""Tests del grid de horario profesional."""

from datetime import time
from uuid import uuid4

import pytest
from app.core.errors import ValidationError
from app.core.professional_hours_grid import (
    allowed_center_slot_keys,
    build_center_period_slot_grids,
    build_professional_hours_grid_context,
    merge_slot_minutes_to_time_ranges,
    parse_working_slots_form,
    working_hours_to_selected_slot_keys,
)
from app.schemas.scheduling import BusinessHourRead, ProfessionalWorkingHourRead


def _business_hour(weekday: int, sort_order: int, opens: str, closes: str) -> BusinessHourRead:
    return BusinessHourRead(
        id=uuid4(),
        weekday=weekday,
        sort_order=sort_order,
        opens_at=time.fromisoformat(opens),
        closes_at=time.fromisoformat(closes),
    )


def _working_hour(
    weekday: int, sort_order: int, opens: str, closes: str
) -> ProfessionalWorkingHourRead:
    return ProfessionalWorkingHourRead(
        id=uuid4(),
        weekday=weekday,
        sort_order=sort_order,
        opens_at=time.fromisoformat(opens),
        closes_at=time.fromisoformat(closes),
    )


def test_build_center_period_slot_grids_expands_to_granularity_slots() -> None:
    hours = [_business_hour(0, 0, "09:00", "10:00")]
    periods = build_center_period_slot_grids(hours, 15)
    assert len(periods) == 1
    assert periods[0].slots == ((540, "09:00"), (555, "09:15"), (570, "09:30"), (585, "09:45"))


def test_working_hours_to_selected_slot_keys() -> None:
    working = [_working_hour(0, 0, "09:00", "09:30")]
    selected = working_hours_to_selected_slot_keys(working, 15)
    assert selected == frozenset({"0:540", "0:555"})


def test_merge_slot_minutes_to_time_ranges_groups_consecutive() -> None:
    ranges = merge_slot_minutes_to_time_ranges([540, 555, 600, 615], 15)
    assert ranges == [(time(9, 0), time(9, 30)), (time(10, 0), time(10, 30))]


def test_parse_working_slots_form_persists_ranges() -> None:
    hours = [_business_hour(0, 0, "09:00", "10:00")]
    periods = build_center_period_slot_grids(hours, 15)
    allowed = allowed_center_slot_keys(periods)
    payload = parse_working_slots_form(
        ["0:540", "0:555", "0:570"],
        allowed_keys=allowed,
        granularity_minutes=15,
    )
    assert len(payload.slots) == 1
    assert payload.slots[0].opens_at == time(9, 0)
    assert payload.slots[0].closes_at == time(9, 45)


def test_parse_working_slots_form_rejects_outside_center() -> None:
    hours = [_business_hour(0, 0, "09:00", "10:00")]
    allowed = allowed_center_slot_keys(build_center_period_slot_grids(hours, 15))
    with pytest.raises(ValidationError, match="outside center"):
        parse_working_slots_form(["0:600"], allowed_keys=allowed, granularity_minutes=15)


def test_build_professional_hours_grid_context_marks_selected() -> None:
    hours = [_business_hour(0, 0, "09:00", "10:00")]
    working = [_working_hour(0, 0, "09:00", "09:30")]
    ctx = build_professional_hours_grid_context(hours, working, 15)
    assert ctx.has_center_hours is True
    assert "0:540" in ctx.selected_slot_keys
    assert "0:555" in ctx.selected_slot_keys
    assert "0:570" not in ctx.selected_slot_keys
