"""Conversión de fechas entre formularios HTML y Google Calendar API."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.core.datetime_display import display_today, resolve_display_timezone
from app.core.errors import ValidationError

WEEK_DAYS = 7


def parse_week_start(value: str | None) -> date:
    """Parsea YYYY-MM-DD; por defecto hoy en zona de visualización."""
    if value is None or not value.strip():
        return display_today()
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValidationError("Fecha de semana no válida") from exc


def shift_week_start(week_start: date, weeks: int) -> date:
    """Desplaza el inicio de ventana semanal en múltiplos de 7 días."""
    return week_start + timedelta(days=weeks * WEEK_DAYS)


def week_bounds_google_iso(week_start: date) -> tuple[str, str]:
    """Devuelve ``timeMin`` y ``timeMax`` (ISO UTC) para una ventana de 7 días."""
    tz = resolve_display_timezone()
    start_local = datetime.combine(week_start, datetime.min.time(), tzinfo=tz)
    end_local = start_local + timedelta(days=WEEK_DAYS)
    time_min = start_local.astimezone(UTC).isoformat().replace("+00:00", "Z")
    time_max = end_local.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return time_min, time_max


def format_week_range_label(week_start: date) -> str:
    """Etiqueta legible del rango de la semana visible."""
    week_end = week_start + timedelta(days=WEEK_DAYS - 1)
    return f"{week_start.strftime('%d/%m/%Y')} - {week_end.strftime('%d/%m/%Y')}"


def format_calendar_event_time(iso_value: str, fmt: str = "%d/%m/%Y %H:%M") -> str:
    """Formatea start/end de Google Calendar para la UI."""
    if len(iso_value) == 10 and iso_value[4] == "-" and iso_value[7] == "-":
        parsed = date.fromisoformat(iso_value)
        if fmt == "%H:%M":
            return "Todo el día"
        if fmt == "%d/%m/%Y %H:%M":
            return parsed.strftime("%d/%m/%Y")
        return parsed.strftime(fmt)
    normalized = iso_value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValidationError("Invalid calendar event datetime") from exc
    if parsed.tzinfo:
        parsed = parsed.astimezone(resolve_display_timezone())
    else:
        parsed = parsed.replace(tzinfo=UTC).astimezone(resolve_display_timezone())
    return parsed.strftime(fmt)


def calendar_event_date_chip(iso_value: str) -> dict[str, str]:
    """Devuelve día y mes corto para la ficha del evento."""
    months_es = (
        "ENE",
        "FEB",
        "MAR",
        "ABR",
        "MAY",
        "JUN",
        "JUL",
        "AGO",
        "SEP",
        "OCT",
        "NOV",
        "DIC",
    )
    if len(iso_value) == 10 and iso_value[4] == "-" and iso_value[7] == "-":
        parsed = date.fromisoformat(iso_value)
    else:
        normalized = iso_value.replace("Z", "+00:00")
        parsed_dt = datetime.fromisoformat(normalized)
        if parsed_dt.tzinfo:
            parsed = parsed_dt.astimezone(resolve_display_timezone()).date()
        else:
            parsed = parsed_dt.replace(tzinfo=UTC).astimezone(resolve_display_timezone()).date()
    return {
        "day": parsed.strftime("%d"),
        "month": months_es[parsed.month - 1],
    }


def calendar_event_weekday(iso_value: str) -> int:
    """Devuelve el día de la semana del evento (0=lunes, 6=domingo)."""
    if len(iso_value) == 10 and iso_value[4] == "-" and iso_value[7] == "-":
        return date.fromisoformat(iso_value).weekday()
    normalized = iso_value.replace("Z", "+00:00")
    parsed_dt = datetime.fromisoformat(normalized)
    if parsed_dt.tzinfo:
        local = parsed_dt.astimezone(resolve_display_timezone())
    else:
        local = parsed_dt.replace(tzinfo=UTC).astimezone(resolve_display_timezone())
    return local.date().weekday()


def calendar_event_is_all_day(iso_value: str) -> bool:
    """True si el evento es de día completo (solo fecha)."""
    return len(iso_value) == 10 and iso_value[4] == "-" and iso_value[7] == "-"


def local_input_to_google_iso(value: str) -> str:
    """Convierte ``datetime-local`` (sin zona) a ISO 8601 con offset local."""
    raw = value.strip()
    if not raw:
        raise ValidationError("Datetime is required")
    try:
        naive = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError("Invalid datetime format") from exc
    tz = resolve_display_timezone()
    aware = naive.replace(tzinfo=tz)
    return aware.isoformat()


def google_iso_to_local_input(iso_value: str) -> str:
    """Convierte ISO de Google a valor para ``input type=datetime-local``."""
    if len(iso_value) == 10 and iso_value[4] == "-" and iso_value[7] == "-":
        return f"{iso_value}T09:00"
    normalized = iso_value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValidationError("Invalid calendar event datetime") from exc
    tz = resolve_display_timezone()
    local = parsed.astimezone(tz) if parsed.tzinfo else parsed.replace(tzinfo=UTC).astimezone(tz)
    return local.strftime("%Y-%m-%dT%H:%M")
