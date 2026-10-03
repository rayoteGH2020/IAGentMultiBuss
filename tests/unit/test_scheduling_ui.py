"""Tests de scheduling_ui (Paso 30 Fase C)."""

from datetime import UTC, date, datetime, time
from uuid import uuid4

from app.core.scheduling_ui import (
    DayGridRowKind,
    build_day_calendar_grid,
    build_month_calendar_view,
    build_week_calendar_view,
    compute_date_range,
    format_range_label,
    layout_day_appointment_blocks,
    parse_anchor_date,
    shift_anchor,
)
from app.schemas.scheduling import BusinessHourRead


def _business_hour(weekday: int, sort_order: int, opens: str, closes: str) -> BusinessHourRead:
    return BusinessHourRead(
        id=uuid4(),
        weekday=weekday,
        sort_order=sort_order,
        opens_at=time.fromisoformat(opens),
        closes_at=time.fromisoformat(closes),
    )


def test_compute_date_range_week_includes_seven_days() -> None:
    anchor = date(2026, 7, 15)
    start, end = compute_date_range("week", anchor)
    assert start == anchor
    assert end == date(2026, 7, 21)


def test_shift_anchor_week_moves_seven_days() -> None:
    anchor = date(2026, 7, 15)
    assert shift_anchor("week", anchor, 1) == date(2026, 7, 22)


def test_format_range_label_week() -> None:
    label = format_range_label("week", date(2026, 7, 15), date(2026, 7, 21))
    assert "15" in label and "21" in label


def test_format_range_label_day_today_shows_hoy(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.scheduling_ui.display_today",
        lambda _tz=None: date(2026, 7, 13),
    )
    label = format_range_label(
        "day", date(2026, 7, 13), date(2026, 7, 13), timezone="Europe/Madrid"
    )
    assert label.startswith("Hoy")


def test_parse_anchor_date_invalid_falls_back_to_today() -> None:
    parsed = parse_anchor_date("not-a-date", "Europe/Madrid")
    assert isinstance(parsed, date)


def test_build_day_calendar_grid_morning_afternoon_with_break() -> None:
    hours = [
        _business_hour(0, 0, "09:00", "14:00"),
        _business_hour(0, 1, "16:00", "21:00"),
    ]
    grid = build_day_calendar_grid(hours, weekday=0, granularity_minutes=15)
    kinds = [row.kind for row in grid.rows]
    assert DayGridRowKind.period_header in kinds
    assert DayGridRowKind.break_separator in kinds
    assert DayGridRowKind.time_slot in kinds
    slot_labels = [row.time_label for row in grid.rows if row.kind == DayGridRowKind.time_slot]
    assert slot_labels[0] == "09:00"
    assert "13:45" in slot_labels
    assert "16:00" in slot_labels
    assert "20:45" in slot_labels


def test_build_day_calendar_grid_closed_day() -> None:
    grid = build_day_calendar_grid([], weekday=0, granularity_minutes=15, is_closed_day=True)
    assert grid.is_closed
    assert grid.closed_message == "Centro cerrado este día"


def test_layout_day_appointment_blocks_positions_on_grid() -> None:
    hours = [_business_hour(0, 0, "09:00", "14:00")]
    grid = build_day_calendar_grid(hours, weekday=0, granularity_minutes=15)
    day = date(2026, 7, 13)

    class _Appt:
        def __init__(self) -> None:
            self.id = uuid4()
            self.professional_id = uuid4()
            self.client_name = "Ana"
            self.status = type("S", (), {"value": "scheduled"})()
            tz = UTC
            self.start_at = datetime(2026, 7, 13, 9, 0, tzinfo=tz)
            self.end_at = datetime(2026, 7, 13, 9, 30, tzinfo=tz)

    blocks = layout_day_appointment_blocks([_Appt()], grid, day, "UTC")
    assert len(blocks) == 1
    assert blocks[0].top_px == grid.rows[0].height_px
    assert blocks[0].height_px >= grid.slot_height_px


def test_build_week_calendar_view_has_seven_columns_and_rows() -> None:
    hours = [
        _business_hour(0, 0, "09:00", "14:00"),
        _business_hour(0, 1, "16:00", "21:00"),
    ]
    week_start = date(2026, 7, 13)
    week = build_week_calendar_view(
        hours,
        week_start,
        closed_dates=set(),
        appointments=[],
        timezone="Europe/Madrid",
        granularity_minutes=15,
        today=week_start,
    )
    assert len(week.days) == 7
    assert len(week.rows) > 0
    assert week.days[0].open_minutes
    assert week.days[0].day == week_start


def test_build_month_calendar_view_includes_padding_and_open_periods() -> None:
    hours = [_business_hour(2, 0, "09:00", "14:00")]
    month_start = date(2026, 7, 1)
    month_end = date(2026, 7, 31)
    month = build_month_calendar_view(
        hours,
        month_start,
        month_end,
        closed_dates=set(),
        appointments=[],
        timezone="Europe/Madrid",
        granularity_minutes=15,
        today=date(2026, 7, 15),
    )
    assert len(month.cells) % 7 == 0
    july_first = next(c for c in month.cells if c.day == month_start)
    assert "09:00" in july_first.open_periods[0]
    assert july_first.time_rows
