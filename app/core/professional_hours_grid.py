"""Grid de tramos del horario profesional (marcar/desmarcar sobre horario del centro)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import time
from typing import TYPE_CHECKING

from app.core.business_hours_validation import iter_grid_slot_times
from app.core.errors import ValidationError
from app.core.scheduling_ui import (
    PERIOD_LABELS,
    WEEKDAY_LABELS,
    _BusinessHourLike,
    _time_to_minutes,
)
from app.schemas.scheduling import (
    ProfessionalWorkingHourRead,
    ProfessionalWorkingHourSlotUpdate,
    ProfessionalWorkingHoursUpdate,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


MAX_RANGES_PER_WEEKDAY = 4


@dataclass(frozen=True)
class CenterPeriodSlotGrid:
    """Tramo del centro (mañana/tarde) desglosado en slots de granularidad."""

    weekday: int
    sort_order: int
    period_label: str
    period_range: str
    slots: tuple[tuple[int, str], ...]  # (minutes_from_midnight, "HH:MM")


@dataclass(frozen=True)
class ProfessionalHoursGridContext:
    """Contexto para pintar el grid semanal del profesional."""

    granularity_minutes: int
    periods_by_weekday: dict[int, tuple[CenterPeriodSlotGrid, ...]]
    selected_slot_keys: frozenset[str]
    has_center_hours: bool


def _slot_key(weekday: int, minutes: int) -> str:
    return f"{weekday}:{minutes}"


def _minutes_to_time_label(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def build_center_period_slot_grids(
    business_hours: Sequence[_BusinessHourLike],
    granularity_minutes: int,
) -> tuple[CenterPeriodSlotGrid, ...]:
    """Franjas del centro expandidas a slots de reserva."""
    grids: list[CenterPeriodSlotGrid] = []
    for weekday in range(7):
        day_rows = [
            row
            for row in business_hours
            if row.weekday == weekday and row.opens_at is not None and row.closes_at is not None
        ]
        day_rows.sort(key=lambda row: row.sort_order)
        for row in day_rows:
            assert row.opens_at is not None and row.closes_at is not None
            slot_times = iter_grid_slot_times(row.opens_at, row.closes_at, granularity_minutes)
            slots = tuple(
                (_time_to_minutes(slot_time), slot_time.strftime("%H:%M"))
                for slot_time in slot_times
            )
            period_label = PERIOD_LABELS.get(row.sort_order, f"Franja {row.sort_order + 1}")
            period_range = f"{row.opens_at.strftime('%H:%M')} - {row.closes_at.strftime('%H:%M')}"
            grids.append(
                CenterPeriodSlotGrid(
                    weekday=weekday,
                    sort_order=row.sort_order,
                    period_label=period_label,
                    period_range=period_range,
                    slots=slots,
                )
            )
    return tuple(grids)


def working_hours_to_selected_slot_keys(
    working_hours: Sequence[ProfessionalWorkingHourRead],
    granularity_minutes: int,
) -> frozenset[str]:
    """Convierte rangos guardados del profesional a claves de slot."""
    selected: set[str] = set()
    for row in working_hours:
        if row.opens_at is None or row.closes_at is None:
            continue
        for slot_time in iter_grid_slot_times(row.opens_at, row.closes_at, granularity_minutes):
            selected.add(_slot_key(row.weekday, _time_to_minutes(slot_time)))
    return frozenset(selected)


def allowed_center_slot_keys(
    period_grids: tuple[CenterPeriodSlotGrid, ...],
) -> frozenset[str]:
    keys: set[str] = set()
    for period in period_grids:
        for minutes, _label in period.slots:
            keys.add(_slot_key(period.weekday, minutes))
    return frozenset(keys)


def merge_slot_minutes_to_time_ranges(
    minutes: list[int],
    granularity_minutes: int,
) -> list[tuple[time, time]]:
    """Agrupa minutos consecutivos en rangos [opens_at, closes_at)."""
    if not minutes:
        return []
    ordered = sorted(set(minutes))
    ranges_minutes: list[tuple[int, int]] = []
    start = ordered[0]
    previous = ordered[0]
    for minute in ordered[1:]:
        if minute == previous + granularity_minutes:
            previous = minute
            continue
        ranges_minutes.append((start, previous + granularity_minutes))
        start = minute
        previous = minute
    ranges_minutes.append((start, previous + granularity_minutes))
    return [
        (time(opens // 60, opens % 60), time(closes // 60, closes % 60))
        for opens, closes in ranges_minutes
    ]


def parse_working_slots_form(
    raw_values: list[str],
    *,
    allowed_keys: frozenset[str],
    granularity_minutes: int,
) -> ProfessionalWorkingHoursUpdate:
    """Parsea checkboxes ``working_slots`` → rangos persistibles."""
    selected_by_weekday: dict[int, list[int]] = defaultdict(list)
    for raw in raw_values:
        if ":" not in raw:
            raise ValidationError("Invalid working slot value")
        weekday_str, minutes_str = raw.split(":", 1)
        try:
            weekday = int(weekday_str)
            minutes = int(minutes_str)
        except ValueError as exc:
            raise ValidationError("Invalid working slot value") from exc
        key = _slot_key(weekday, minutes)
        if key not in allowed_keys:
            raise ValidationError("Working slot outside center business hours")
        selected_by_weekday[weekday].append(minutes)

    slots: list[ProfessionalWorkingHourSlotUpdate] = []
    for weekday in range(7):
        ranges = merge_slot_minutes_to_time_ranges(
            selected_by_weekday.get(weekday, []),
            granularity_minutes,
        )
        if len(ranges) > MAX_RANGES_PER_WEEKDAY:
            day_label = WEEKDAY_LABELS[weekday]
            raise ValidationError(
                f"{day_label}: demasiados tramos disjuntos (máximo {MAX_RANGES_PER_WEEKDAY})"
            )
        for sort_order, (opens_at, closes_at) in enumerate(ranges):
            slots.append(
                ProfessionalWorkingHourSlotUpdate(
                    weekday=weekday,
                    sort_order=sort_order,
                    opens_at=opens_at,
                    closes_at=closes_at,
                )
            )

    return ProfessionalWorkingHoursUpdate(slots=slots)


def build_professional_hours_grid_context(
    business_hours: Sequence[_BusinessHourLike],
    working_hours: Sequence[ProfessionalWorkingHourRead],
    granularity_minutes: int,
) -> ProfessionalHoursGridContext:
    """Arma el contexto del template del grid profesional."""
    period_grids = build_center_period_slot_grids(business_hours, granularity_minutes)
    periods_by_weekday: dict[int, list[CenterPeriodSlotGrid]] = {day: [] for day in range(7)}
    for period in period_grids:
        periods_by_weekday[period.weekday].append(period)

    selected = working_hours_to_selected_slot_keys(working_hours, granularity_minutes)
    allowed = allowed_center_slot_keys(period_grids)
    # Solo claves válidas (por si el centro cambió desde la última edición)
    selected = frozenset(key for key in selected if key in allowed)

    return ProfessionalHoursGridContext(
        granularity_minutes=granularity_minutes,
        periods_by_weekday={day: tuple(items) for day, items in periods_by_weekday.items()},
        selected_slot_keys=selected,
        has_center_hours=bool(period_grids),
    )
