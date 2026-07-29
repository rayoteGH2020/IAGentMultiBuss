"""Parseo de formularios HTMX de scheduling (horarios del centro y profesionales)."""

from __future__ import annotations

from datetime import time

from app.core.errors import ValidationError
from app.schemas.scheduling import (
    BusinessHourSlotUpdate,
    BusinessHoursUpdate,
    ProfessionalWorkingHourSlotUpdate,
    ProfessionalWorkingHoursUpdate,
)

_PERIOD_LABELS = ("mañana", "tarde")


def parse_optional_time(value: str | None) -> time | None:
    """Parsea HH:MM o devuelve None si está vacío; error claro si el formato es inválido."""
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip()
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValidationError(f"Hora inválida «{raw}»; usa HH:MM")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ValidationError(f"Hora inválida «{raw}»; usa HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValidationError(f"Hora fuera de rango «{raw}»")
    return time(hour, minute)


def _validate_period_pair(
    *,
    weekday: int,
    sort_order: int,
    opens_raw: str | None,
    closes_raw: str | None,
    weekday_labels: tuple[str, ...],
) -> tuple[time | None, time | None]:
    opens_empty = not (opens_raw and str(opens_raw).strip())
    closes_empty = not (closes_raw and str(closes_raw).strip())
    day_label = weekday_labels[weekday] if 0 <= weekday < len(weekday_labels) else str(weekday)
    period_label = _PERIOD_LABELS[sort_order] if 0 <= sort_order < len(_PERIOD_LABELS) else "tramo"

    if opens_empty != closes_empty:
        raise ValidationError(
            f"{day_label}: indica inicio y fin del horario de {period_label}, o déjalo vacío"
        )

    opens_at = parse_optional_time(opens_raw)
    closes_at = parse_optional_time(closes_raw)

    if opens_at is not None and closes_at is not None and opens_at >= closes_at:
        raise ValidationError(
            f"{day_label} ({period_label}): la hora de inicio debe ser anterior a la de fin"
        )

    return opens_at, closes_at


def parse_business_hours_form(
    *,
    weekday_forms: list[tuple[int, int, str | None, str | None]],
    weekday_labels: tuple[str, ...],
) -> BusinessHoursUpdate:
    """Construye BusinessHoursUpdate validando pares De/A por mañana y tarde."""
    slots: list[BusinessHourSlotUpdate] = []
    for weekday, sort_order, opens_raw, closes_raw in weekday_forms:
        opens_at, closes_at = _validate_period_pair(
            weekday=weekday,
            sort_order=sort_order,
            opens_raw=opens_raw,
            closes_raw=closes_raw,
            weekday_labels=weekday_labels,
        )
        slots.append(
            BusinessHourSlotUpdate(
                weekday=weekday,
                sort_order=sort_order,
                opens_at=opens_at,
                closes_at=closes_at,
            )
        )
    return BusinessHoursUpdate(slots=slots)


def parse_working_hours_form(
    *,
    weekday_forms: list[tuple[int, int, str | None, str | None]],
    weekday_labels: tuple[str, ...],
) -> ProfessionalWorkingHoursUpdate:
    """Construye ProfessionalWorkingHoursUpdate con la misma regla De/A."""
    slots: list[ProfessionalWorkingHourSlotUpdate] = []
    for weekday, sort_order, opens_raw, closes_raw in weekday_forms:
        opens_at, closes_at = _validate_period_pair(
            weekday=weekday,
            sort_order=sort_order,
            opens_raw=opens_raw,
            closes_raw=closes_raw,
            weekday_labels=weekday_labels,
        )
        slots.append(
            ProfessionalWorkingHourSlotUpdate(
                weekday=weekday,
                sort_order=sort_order,
                opens_at=opens_at,
                closes_at=closes_at,
            )
        )
    return ProfessionalWorkingHoursUpdate(slots=slots)
