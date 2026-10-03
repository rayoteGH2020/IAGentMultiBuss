"""Clave de agrupación por vencimiento (``fecha_fin``) para contratos y seguros."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Label, func

from app.schemas.document_query import AggregateGroupBy

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.orm import InstrumentedAttribute

NO_EXPIRY_KEY = "(sin vencimiento)"


def expiry_group_key(
    fecha_fin: InstrumentedAttribute[date | None],
    group_by: AggregateGroupBy,
) -> Label[Any]:
    """``YYYY-MM`` o ``YYYY`` de ``fecha_fin``; los que no vencen van en su propio grupo.

    Sin el ``coalesce`` quedarían con clave vacía y el chat los confundiría con
    un mes sin nombre.
    """
    fmt = "YYYY-MM" if group_by == AggregateGroupBy.expiry_month else "YYYY"
    return func.coalesce(func.to_char(fecha_fin, fmt), NO_EXPIRY_KEY).label("group_key")
