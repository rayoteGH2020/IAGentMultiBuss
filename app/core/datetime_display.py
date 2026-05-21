"""Formateo de timestamps UTC para la UI en zona horaria local."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import get_settings

DEFAULT_DISPLAY_TIMEZONE = "Europe/Madrid"


def resolve_display_timezone(timezone: str | None = None) -> ZoneInfo:
    """Resuelve la zona horaria de visualización (tenant override o config app)."""
    candidate = (timezone or "").strip()
    if candidate:
        try:
            return ZoneInfo(candidate)
        except ZoneInfoNotFoundError:
            pass
    return ZoneInfo(get_settings().app_display_timezone)


def local_datetime(
    value: datetime | None,
    fmt: str = "%d/%m/%Y %H:%M",
    timezone: str | None = None,
) -> str:
    """Convierte un datetime (UTC en BD) a hora local y lo formatea para Jinja."""
    if value is None:
        return "—"

    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    local = aware.astimezone(resolve_display_timezone(timezone))
    return local.strftime(fmt)


def display_today(timezone: str | None = None) -> date:
    """Fecha de hoy en la zona horaria de visualización de la app."""
    tz = resolve_display_timezone(timezone)
    return datetime.now(tz).date()
