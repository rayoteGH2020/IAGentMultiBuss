"""Persistencia de ``activity_log`` (D029): inserción en bloque y purga por retención."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import structlog
from sqlalchemy import text

from app.config import get_settings
from app.core.activity.buffer import ActivityFlusher
from app.core.db import get_sessionmaker
from app.models import ActivityLog

if TYPE_CHECKING:
    from sqlalchemy import Table

logger = structlog.get_logger(__name__)

PURGE_BATCH_ROWS = 50_000


async def insert_batch(rows: list[dict[str, Any]]) -> None:
    """INSERT en bloque en su propia transacción, fuera de la de la petición.

    Core ``insert`` sin ``RETURNING``: ``saas_app`` no tiene SELECT sobre la tabla.
    """
    table = cast("Table", ActivityLog.__table__)
    # executemany exige las mismas claves en todas las filas, y cada tipo de fila
    # (request, job, event, error) rellena columnas distintas.
    columns = [column.name for column in table.columns if column.name != "id"]
    normalized = [{name: row.get(name) for name in columns} for row in rows]
    async with get_sessionmaker()() as session:
        await session.execute(table.insert(), normalized)
        await session.commit()


def start_flusher() -> ActivityFlusher | None:
    """Arranca el volcado periódico del proceso; ``None`` si el registro está apagado."""
    if not get_settings().activity_log_enabled:
        return None
    flusher = ActivityFlusher(insert_batch)
    flusher.start()
    return flusher


async def purge_expired(retention_days: int, *, batch_size: int = PURGE_BATCH_ROWS) -> int:
    """Borra por lotes las filas más antiguas que la retención. Devuelve cuántas.

    Cada lote es una transacción corta (``purge_activity_log`` en BD, que exige
    ``retention_days >= 7``), para no bloquear la tabla con un DELETE enorme.
    """
    total = 0
    while True:
        async with get_sessionmaker()() as session:
            result = await session.execute(
                text("SELECT purge_activity_log(:days, :batch)"),
                {"days": retention_days, "batch": batch_size},
            )
            deleted = int(result.scalar_one())
            await session.commit()
        total += deleted
        if deleted < batch_size:
            return total
