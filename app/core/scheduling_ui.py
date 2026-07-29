"""Helpers de UI para calendario interno (Paso 30 Fase C)."""

from __future__ import annotations

import calendar
import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Protocol
from uuid import UUID

from app.core.business_hours_validation import iter_grid_slot_times
from app.core.datetime_display import display_today, resolve_display_timezone

WEEKDAY_LABELS = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
MONTH_LABELS = (
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
)

SLOT_HEIGHT_PX = 28
MINI_SLOT_HEIGHT_PX = 12
BREAK_HEIGHT_PX = 52
PERIOD_HEADER_HEIGHT_PX = 36
PERIOD_LABELS = {0: "Mañana", 1: "Tarde"}


class DayGridRowKind(enum.StrEnum):
    period_header = "period_header"
    time_slot = "time_slot"
    break_separator = "break_separator"


@dataclass(frozen=True)
class DayGridRow:
    kind: DayGridRowKind
    height_px: int
    time_label: str | None = None
    period_label: str | None = None
    period_range: str | None = None
    break_label: str | None = None
    minutes_from_midnight: int | None = None
    is_hour_start: bool = False


@dataclass(frozen=True)
class DayCalendarGrid:
    rows: tuple[DayGridRow, ...]
    granularity_minutes: int
    slot_height_px: int
    is_closed: bool
    closed_message: str | None = None

    @property
    def total_height_px(self) -> int:
        return sum(row.height_px for row in self.rows)


@dataclass(frozen=True)
class DayAppointmentBlock:
    appointment_id: UUID
    professional_id: UUID | None
    client_name: str
    status: str
    time_label: str
    top_px: int
    height_px: int
    color: str | None = None


@dataclass(frozen=True)
class UnifiedTimeRow:
    minutes_from_midnight: int
    time_label: str
    is_hour_start: bool
    height_px: int = SLOT_HEIGHT_PX


@dataclass(frozen=True)
class WeekDayColumn:
    day: date
    weekday_label: str
    day_number: int
    is_today: bool
    is_closed: bool
    closed_message: str | None
    open_minutes: frozenset[int]
    blocks_by_prof: dict[str, list[DayAppointmentBlock]]


@dataclass(frozen=True)
class WeekCalendarView:
    rows: tuple[UnifiedTimeRow, ...]
    days: tuple[WeekDayColumn, ...]
    granularity_minutes: int
    slot_height_px: int

    @property
    def total_height_px(self) -> int:
        return sum(row.height_px for row in self.rows)


@dataclass(frozen=True)
class MonthDayCell:
    day: date | None
    day_number: int | None
    weekday_label: str | None
    is_today: bool
    is_closed: bool
    closed_message: str | None
    open_periods: tuple[str, ...]
    open_minutes: frozenset[int]
    time_rows: tuple[UnifiedTimeRow, ...]
    appointments: tuple[DayAppointmentBlock, ...]


@dataclass(frozen=True)
class MonthCalendarView:
    cells: tuple[MonthDayCell, ...]
    granularity_minutes: int
    mini_slot_height_px: int


class _BusinessHourLike(Protocol):
    @property
    def weekday(self) -> int: ...

    @property
    def sort_order(self) -> int: ...

    @property
    def opens_at(self) -> time | None: ...

    @property
    def closes_at(self) -> time | None: ...


class _AppointmentLike(Protocol):
    @property
    def id(self) -> UUID: ...

    @property
    def professional_id(self) -> UUID | None: ...

    @property
    def client_name(self) -> str: ...

    @property
    def status(self) -> object: ...

    @property
    def start_at(self) -> datetime: ...

    @property
    def end_at(self) -> datetime: ...


def _time_to_minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _format_time(value: time) -> str:
    return value.strftime("%H:%M")


def _iter_slot_times(opens: time, closes: time, granularity_minutes: int) -> list[time]:
    return iter_grid_slot_times(opens, closes, granularity_minutes)


def _collect_open_minutes_from_grid(grid: DayCalendarGrid) -> frozenset[int]:
    return frozenset(
        row.minutes_from_midnight
        for row in grid.rows
        if row.kind == DayGridRowKind.time_slot and row.minutes_from_midnight is not None
    )


def _open_period_labels_from_grid(grid: DayCalendarGrid) -> tuple[str, ...]:
    labels: list[str] = []
    for row in grid.rows:
        if row.kind == DayGridRowKind.period_header and row.period_range:
            labels.append(row.period_range)
    return tuple(labels)


def build_unified_time_rows(
    business_hours: Sequence[_BusinessHourLike],
    dates: list[date],
    closed_dates: set[date],
    granularity_minutes: int,
) -> tuple[UnifiedTimeRow, ...]:
    """Filas horarias unificadas (unión de franjas abiertas en el rango de fechas)."""
    all_minutes: set[int] = set()
    for day in dates:
        if day in closed_dates:
            continue
        grid = build_day_calendar_grid(
            business_hours,
            day.weekday(),
            granularity_minutes=granularity_minutes,
        )
        if not grid.is_closed:
            all_minutes.update(_collect_open_minutes_from_grid(grid))
    return tuple(
        UnifiedTimeRow(
            minutes_from_midnight=minutes,
            time_label=f"{minutes // 60:02d}:{minutes % 60:02d}",
            is_hour_start=minutes % 60 == 0,
        )
        for minutes in sorted(all_minutes)
    )


def _offset_on_unified_rows(rows: tuple[UnifiedTimeRow, ...], minutes: int) -> int | None:
    top = 0
    matched = False
    for row in rows:
        if row.minutes_from_midnight == minutes:
            matched = True
            break
        if row.minutes_from_midnight > minutes:
            return None
        top += row.height_px
    return top if matched else None


def layout_blocks_on_unified_rows(
    appointments: Sequence[_AppointmentLike],
    rows: tuple[UnifiedTimeRow, ...],
    day: date,
    timezone: str | None,
    *,
    slot_height_px: int = SLOT_HEIGHT_PX,
    granularity_minutes: int = 15,
    professional_colors: dict[UUID, str] | None = None,
) -> list[DayAppointmentBlock]:
    """Posiciona citas sobre filas horarias unificadas."""
    if not rows:
        return []

    tz = resolve_display_timezone(timezone)
    colors = professional_colors or {}
    blocks: list[DayAppointmentBlock] = []

    for appointment in appointments:
        local_start = appointment.start_at.astimezone(tz)
        if local_start.date() != day:
            continue

        local_end = appointment.end_at.astimezone(tz)
        start_minutes = _time_to_minutes(local_start.time())
        end_minutes = _time_to_minutes(local_end.time())
        top_px = _offset_on_unified_rows(rows, start_minutes)
        if top_px is None:
            continue

        duration_minutes = max(end_minutes - start_minutes, granularity_minutes)
        height_px = max(
            slot_height_px,
            int((duration_minutes / granularity_minutes) * slot_height_px),
        )
        status = getattr(appointment.status, "value", str(appointment.status))
        prof_id = appointment.professional_id
        blocks.append(
            DayAppointmentBlock(
                appointment_id=appointment.id,
                professional_id=prof_id,
                client_name=appointment.client_name,
                status=status,
                time_label=f"{local_start.strftime('%H:%M')}-{local_end.strftime('%H:%M')}",
                top_px=top_px,
                height_px=height_px,
                color=colors.get(prof_id) if prof_id else None,
            )
        )

    return blocks


def build_week_calendar_view(
    business_hours: Sequence[_BusinessHourLike],
    week_start: date,
    *,
    closed_dates: set[date],
    appointments: Sequence[_AppointmentLike],
    timezone: str | None,
    granularity_minutes: int,
    today: date,
    professional_colors: dict[UUID, str] | None = None,
) -> WeekCalendarView:
    """Vista semana: 7 columnas con eje temporal unificado."""
    week_days = [week_start + timedelta(days=offset) for offset in range(7)]
    unified_rows = build_unified_time_rows(
        business_hours,
        week_days,
        closed_dates,
        granularity_minutes,
    )

    columns: list[WeekDayColumn] = []
    for day in week_days:
        grid = build_day_calendar_grid(
            business_hours,
            day.weekday(),
            granularity_minutes=granularity_minutes,
            is_closed_day=day in closed_dates,
        )
        open_minutes = _collect_open_minutes_from_grid(grid) if not grid.is_closed else frozenset()
        blocks = layout_blocks_on_unified_rows(
            appointments,
            unified_rows,
            day,
            timezone,
            granularity_minutes=granularity_minutes,
            professional_colors=professional_colors,
        )
        columns.append(
            WeekDayColumn(
                day=day,
                weekday_label=WEEKDAY_LABELS[day.weekday()],
                day_number=day.day,
                is_today=day == today,
                is_closed=grid.is_closed,
                closed_message=grid.closed_message,
                open_minutes=open_minutes,
                blocks_by_prof=group_blocks_by_professional(blocks),
            )
        )

    return WeekCalendarView(
        rows=unified_rows,
        days=tuple(columns),
        granularity_minutes=granularity_minutes,
        slot_height_px=SLOT_HEIGHT_PX,
    )


def build_month_calendar_view(
    business_hours: Sequence[_BusinessHourLike],
    month_start: date,
    month_end: date,
    *,
    closed_dates: set[date],
    appointments: Sequence[_AppointmentLike],
    timezone: str | None,
    granularity_minutes: int,
    today: date,
    professional_colors: dict[UUID, str] | None = None,
) -> MonthCalendarView:
    """Vista mes: rejilla mensual con mini-grid vertical por día."""
    month_days = [
        month_start + timedelta(days=offset) for offset in range((month_end - month_start).days + 1)
    ]
    unified_rows = build_unified_time_rows(
        business_hours,
        month_days,
        closed_dates,
        granularity_minutes,
    )

    cells: list[MonthDayCell] = []
    for _ in range(month_start.weekday()):
        cells.append(
            MonthDayCell(
                day=None,
                day_number=None,
                weekday_label=None,
                is_today=False,
                is_closed=False,
                closed_message=None,
                open_periods=(),
                open_minutes=frozenset(),
                time_rows=(),
                appointments=(),
            )
        )

    for day in month_days:
        grid = build_day_calendar_grid(
            business_hours,
            day.weekday(),
            granularity_minutes=granularity_minutes,
            is_closed_day=day in closed_dates,
        )
        open_minutes = _collect_open_minutes_from_grid(grid) if not grid.is_closed else frozenset()
        day_rows = tuple(
            UnifiedTimeRow(
                minutes_from_midnight=row.minutes_from_midnight,
                time_label=row.time_label,
                is_hour_start=row.is_hour_start,
                height_px=MINI_SLOT_HEIGHT_PX,
            )
            for row in unified_rows
            if row.minutes_from_midnight in open_minutes
        )
        mini_blocks = layout_blocks_on_unified_rows(
            appointments,
            day_rows,
            day,
            timezone,
            slot_height_px=MINI_SLOT_HEIGHT_PX,
            granularity_minutes=granularity_minutes,
            professional_colors=professional_colors,
        )
        cells.append(
            MonthDayCell(
                day=day,
                day_number=day.day,
                weekday_label=WEEKDAY_LABELS[day.weekday()],
                is_today=day == today,
                is_closed=grid.is_closed,
                closed_message=grid.closed_message,
                open_periods=_open_period_labels_from_grid(grid),
                open_minutes=open_minutes,
                time_rows=day_rows,
                appointments=tuple(mini_blocks),
            )
        )

    trailing = (7 - (len(cells) % 7)) % 7
    for _ in range(trailing):
        cells.append(
            MonthDayCell(
                day=None,
                day_number=None,
                weekday_label=None,
                is_today=False,
                is_closed=False,
                closed_message=None,
                open_periods=(),
                open_minutes=frozenset(),
                time_rows=(),
                appointments=(),
            )
        )

    return MonthCalendarView(
        cells=tuple(cells),
        granularity_minutes=granularity_minutes,
        mini_slot_height_px=MINI_SLOT_HEIGHT_PX,
    )


def build_day_calendar_grid(
    business_hours: Sequence[_BusinessHourLike],
    weekday: int,
    *,
    granularity_minutes: int,
    is_closed_day: bool = False,
) -> DayCalendarGrid:
    """Construye filas del eje vertical (día) según horario del centro."""
    if is_closed_day:
        return DayCalendarGrid(
            rows=(),
            granularity_minutes=granularity_minutes,
            slot_height_px=SLOT_HEIGHT_PX,
            is_closed=True,
            closed_message="Centro cerrado este día",
        )

    day_slots = [
        row
        for row in business_hours
        if row.weekday == weekday and row.opens_at is not None and row.closes_at is not None
    ]
    day_slots.sort(key=lambda row: row.sort_order)

    if not day_slots:
        return DayCalendarGrid(
            rows=(),
            granularity_minutes=granularity_minutes,
            slot_height_px=SLOT_HEIGHT_PX,
            is_closed=True,
            closed_message="Sin horario configurado para este día",
        )

    rows: list[DayGridRow] = []
    for index, slot in enumerate(day_slots):
        assert slot.opens_at is not None and slot.closes_at is not None
        if index > 0:
            previous = day_slots[index - 1]
            assert previous.closes_at is not None and slot.opens_at is not None
            rows.append(
                DayGridRow(
                    kind=DayGridRowKind.break_separator,
                    height_px=BREAK_HEIGHT_PX,
                    break_label=f"{_format_time(previous.closes_at)} - {_format_time(slot.opens_at)}",
                )
            )

        period_label = PERIOD_LABELS.get(slot.sort_order, f"Franja {slot.sort_order + 1}")
        rows.append(
            DayGridRow(
                kind=DayGridRowKind.period_header,
                height_px=PERIOD_HEADER_HEIGHT_PX,
                period_label=period_label,
                period_range=f"{_format_time(slot.opens_at)} - {_format_time(slot.closes_at)}",
            )
        )

        for slot_time in _iter_slot_times(slot.opens_at, slot.closes_at, granularity_minutes):
            rows.append(
                DayGridRow(
                    kind=DayGridRowKind.time_slot,
                    height_px=SLOT_HEIGHT_PX,
                    time_label=_format_time(slot_time),
                    minutes_from_midnight=_time_to_minutes(slot_time),
                    is_hour_start=slot_time.minute == 0,
                )
            )

    return DayCalendarGrid(
        rows=tuple(rows),
        granularity_minutes=granularity_minutes,
        slot_height_px=SLOT_HEIGHT_PX,
        is_closed=False,
    )


def _row_offset_for_minutes(rows: tuple[DayGridRow, ...], minutes: int) -> int | None:
    top = 0
    matched = False
    for row in rows:
        if row.kind == DayGridRowKind.time_slot and row.minutes_from_midnight is not None:
            if row.minutes_from_midnight == minutes:
                matched = True
                break
            if row.minutes_from_midnight > minutes:
                return None
        top += row.height_px
    return top if matched else None


def layout_day_appointment_blocks(
    appointments: Sequence[_AppointmentLike],
    grid: DayCalendarGrid,
    day: date,
    timezone: str | None,
    *,
    professional_colors: dict[UUID, str] | None = None,
) -> list[DayAppointmentBlock]:
    """Posiciona citas del día en el grid vertical."""
    if grid.is_closed or not grid.rows:
        return []

    tz = resolve_display_timezone(timezone)
    colors = professional_colors or {}
    blocks: list[DayAppointmentBlock] = []

    for appointment in appointments:
        local_start = appointment.start_at.astimezone(tz)
        if local_start.date() != day:
            continue

        local_end = appointment.end_at.astimezone(tz)
        start_minutes = _time_to_minutes(local_start.time())
        end_minutes = _time_to_minutes(local_end.time())
        top_px = _row_offset_for_minutes(grid.rows, start_minutes)
        if top_px is None:
            continue

        duration_minutes = max(end_minutes - start_minutes, grid.granularity_minutes)
        height_px = max(
            grid.slot_height_px,
            int((duration_minutes / grid.granularity_minutes) * grid.slot_height_px),
        )
        status = getattr(appointment.status, "value", str(appointment.status))
        prof_id = appointment.professional_id
        blocks.append(
            DayAppointmentBlock(
                appointment_id=appointment.id,
                professional_id=prof_id,
                client_name=appointment.client_name,
                status=status,
                time_label=f"{local_start.strftime('%H:%M')}-{local_end.strftime('%H:%M')}",
                top_px=top_px,
                height_px=height_px,
                color=colors.get(prof_id) if prof_id else None,
            )
        )

    return blocks


def group_blocks_by_professional(
    blocks: list[DayAppointmentBlock],
) -> dict[str, list[DayAppointmentBlock]]:
    grouped: dict[str, list[DayAppointmentBlock]] = {"unassigned": []}
    for block in blocks:
        key = str(block.professional_id) if block.professional_id else "unassigned"
        grouped.setdefault(key, []).append(block)
    return grouped


def parse_anchor_date(raw: str | None, timezone: str | None = None) -> date:
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return display_today(timezone)


def add_months(anchor: date, months: int) -> date:
    month_index = anchor.month - 1 + months
    year = anchor.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(anchor.day, last_day))


def shift_anchor(view: str, anchor: date, direction: int) -> date:
    if view == "day":
        return anchor + timedelta(days=direction)
    if view == "week":
        return anchor + timedelta(days=7 * direction)
    if view == "month":
        return add_months(anchor, direction)
    return anchor


def compute_date_range(view: str, anchor: date) -> tuple[date, date]:
    if view == "day":
        return anchor, anchor
    if view == "week":
        return anchor, anchor + timedelta(days=6)
    if view == "month":
        start = anchor.replace(day=1)
        last = calendar.monthrange(anchor.year, anchor.month)[1]
        return start, date(anchor.year, anchor.month, last)
    return anchor, anchor + timedelta(days=6)


def format_range_label(
    view: str,
    range_start: date,
    range_end: date,
    *,
    timezone: str | None = None,
) -> str:
    if view == "day":
        today = display_today(timezone)
        weekday_name = WEEKDAY_LABELS[range_start.weekday()]
        month_short = MONTH_LABELS[range_start.month - 1][:3].lower()
        if range_start == today:
            return f"Hoy · {weekday_name} {range_start.day} {month_short} {range_start.year}"
        return f"{weekday_name} {range_start.day} {month_short} {range_start.year}"
    if view == "month":
        return f"{MONTH_LABELS[range_start.month - 1]} {range_start.year}"
    if range_start.month == range_end.month:
        return (
            f"{range_start.day}-{range_end.day} "
            f"{MONTH_LABELS[range_start.month - 1][:3].lower()} {range_start.year}"
        )
    return (
        f"{range_start.day} {MONTH_LABELS[range_start.month - 1][:3].lower()} - "
        f"{range_end.day} {MONTH_LABELS[range_end.month - 1][:3].lower()} {range_end.year}"
    )


def range_to_datetimes(
    range_start: date,
    range_end: date,
    timezone: str | None,
) -> tuple[datetime, datetime]:
    tz = resolve_display_timezone(timezone)
    start = datetime.combine(range_start, time.min, tzinfo=tz)
    end = datetime.combine(range_end, time(23, 59, 59), tzinfo=tz)
    return start, end


def is_appointment_past_day(start_at: datetime, timezone: str | None) -> bool:
    local = start_at.astimezone(resolve_display_timezone(timezone))
    return local.date() < display_today(timezone)


def is_appointment_read_only(start_at: datetime, timezone: str | None) -> bool:
    return is_appointment_past_day(start_at, timezone)


def _professional_specialty_ids(prof: object) -> set[UUID]:
    """Acepta ORM Professional o ProfessionalRead (specialty_service_ids)."""
    specialty_service_ids = getattr(prof, "specialty_service_ids", None)
    if specialty_service_ids is not None:
        return set(specialty_service_ids)
    specialties = getattr(prof, "specialties", ())
    return {s.service_id for s in specialties}


def sort_professionals_for_service[T](
    professionals: Sequence[T],
    specialty_service_id: UUID | None,
) -> list[tuple[T, bool]]:
    """Especialistas primero (★) si hay service_id seleccionado."""
    if specialty_service_id is None:
        return [(p, False) for p in professionals]

    specialists: list[tuple[T, bool]] = []
    others: list[tuple[T, bool]] = []
    for prof in professionals:
        specialty_ids = _professional_specialty_ids(prof)
        if specialty_service_id in specialty_ids:
            specialists.append((prof, True))
        else:
            others.append((prof, False))
    return specialists + others


def calendar_hour_slots() -> list[int]:
    """Horas 8-21 para eje vertical del calendario."""
    return list(range(8, 22))
