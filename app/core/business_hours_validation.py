"""Validación de citas contra el horario del centro."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Protocol

from app.core.datetime_display import resolve_display_timezone
from app.core.errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Sequence


class BusinessHourSlot(Protocol):
    @property
    def weekday(self) -> int: ...

    @property
    def opens_at(self) -> time | None: ...

    @property
    def closes_at(self) -> time | None: ...


def iter_grid_slot_times(opens: time, closes: time, granularity_minutes: int) -> list[time]:
    """Horas de inicio de franja visibles en el grid (misma lógica que la vista día)."""
    if opens >= closes or granularity_minutes <= 0:
        return []
    slots: list[time] = []
    cursor = datetime(2000, 1, 1, opens.hour, opens.minute)
    end = datetime(2000, 1, 1, closes.hour, closes.minute)
    step = timedelta(minutes=granularity_minutes)
    while cursor < end:
        slots.append(cursor.time())
        cursor += step
    return slots


def center_hour_ranges_for_weekday(
    business_hours: Sequence[BusinessHourSlot],
    weekday: int,
) -> list[tuple[time, time]]:
    """Devuelve tramos abiertos (opens, closes) del centro para un weekday."""
    ranges: list[tuple[time, time]] = []
    for row in business_hours:
        if row.weekday != weekday:
            continue
        if row.opens_at is None or row.closes_at is None:
            continue
        if row.opens_at >= row.closes_at:
            continue
        ranges.append((row.opens_at, row.closes_at))
    return ranges


def center_grid_start_times(
    business_hours: Sequence[BusinessHourSlot],
    weekday: int,
    granularity_minutes: int,
) -> list[time]:
    """Instantes de inicio de franja del grid para un weekday."""
    times: list[time] = []
    for opens_at, closes_at in center_hour_ranges_for_weekday(business_hours, weekday):
        times.extend(iter_grid_slot_times(opens_at, closes_at, granularity_minutes))
    return times


def build_grid_times_by_weekday(
    business_hours: Sequence[BusinessHourSlot],
    granularity_minutes: int,
) -> dict[int, list[str]]:
    """Mapa weekday → lista de HH:MM válidos en el grid del calendario."""
    return {
        weekday: [
            slot.strftime("%H:%M")
            for slot in center_grid_start_times(
                business_hours,
                weekday,
                granularity_minutes,
            )
        ]
        for weekday in range(7)
    }


def validate_appointment_start_on_grid(
    appointment_date: date,
    start_time: time,
    business_hours: Sequence[BusinessHourSlot],
    granularity_minutes: int,
) -> None:
    """Comprueba que la hora de inicio coincide con una franja del grid."""
    valid_times = center_grid_start_times(
        business_hours,
        appointment_date.weekday(),
        granularity_minutes,
    )
    if start_time not in valid_times:
        raise ValidationError("start_at must match a visible grid slot time")


def parse_appointment_form_start(
    appointment_date: date,
    start_time: str,
    timezone_name: str,
) -> datetime:
    """Combina fecha + HH:MM del formulario en datetime consciente de zona."""
    try:
        slot_time = time.fromisoformat(start_time.strip())
    except ValueError as exc:
        raise ValidationError("Invalid start_time; expected HH:MM") from exc
    tz = resolve_display_timezone(timezone_name)
    return datetime.combine(appointment_date, slot_time, tzinfo=tz)


def validate_appointment_within_center_hours(
    start_at: datetime,
    end_at: datetime,
    business_hours: Sequence[BusinessHourSlot],
    *,
    timezone_name: str,
    closed_dates: set[date] | None = None,
) -> None:
    """Comprueba que la cita cae íntegramente en un tramo del horario del centro."""
    if end_at <= start_at:
        raise ValidationError("Appointment end must be after start")

    tz = resolve_display_timezone(timezone_name)
    local_start = start_at.astimezone(tz)
    local_end = end_at.astimezone(tz)

    if local_start.date() != local_end.date():
        raise ValidationError("Appointment must start and end on the same calendar day")

    day = local_start.date()
    if closed_dates and day in closed_dates:
        raise ValidationError("Center is closed on this date")

    ranges = center_hour_ranges_for_weekday(business_hours, day.weekday())
    if not ranges:
        raise ValidationError("Center is closed on this weekday")

    start_time = local_start.timetz().replace(tzinfo=None)
    end_time = local_end.timetz().replace(tzinfo=None)

    for opens_at, closes_at in ranges:
        if start_time >= opens_at and end_time <= closes_at:
            return

    raise ValidationError("Appointment must fall within center business hours")
