"""Periodo de cómputo de cuotas y presupuesto: mes natural en hora de España (D027).

Todos los contadores mensuales (cupos, presupuesto de IA) usan el mismo periodo:
del día 1 al último día del mes, en la zona horaria de la app. Si un tenant se da
de alta a mitad de mes, las cuotas son las del mes completo (la factura se
prorratea fuera de la app, D016).
"""

from __future__ import annotations

from calendar import monthrange
from datetime import UTC, date, datetime

from app.core.datetime_display import resolve_display_timezone


def local_date(now: datetime | None = None) -> date:
    """Fecha de hoy (o de ``now``) en la zona horaria de la app."""
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(resolve_display_timezone()).date()


def period_start(day: date) -> date:
    """Primer día del periodo (mes natural) que contiene ``day``."""
    return day.replace(day=1)


def period_end(start: date) -> date:
    """Último día del periodo que empieza en ``start``."""
    return start.replace(day=monthrange(start.year, start.month)[1])


def next_period_start(start: date) -> date:
    """Primer día del periodo siguiente al que empieza en ``start``."""
    if start.month == 12:
        return date(start.year + 1, 1, 1)
    return date(start.year, start.month + 1, 1)


def current_period_start(now: datetime | None = None) -> date:
    """Primer día del periodo en curso, en hora de España."""
    return period_start(local_date(now))


def initial_load_window_end(tenant_created_at: datetime) -> date:
    """Último día de la ventana de carga inicial de contratos (D027).

    Va del alta al final del primer mes completo: si el alta es el día 1, termina
    ese mismo mes; si no, al final del mes siguiente (alta 28/10 → 30/11).
    """
    created = local_date(tenant_created_at)
    start = period_start(created)
    if created == start:
        return period_end(start)
    return period_end(next_period_start(start))
