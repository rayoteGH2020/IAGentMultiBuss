"""Retención de ``audit_log`` (Backlog P2c-7).

``audit_log`` es solo inserción para la app; la purga va por la función de BD
``purge_audit_log`` (``SECURITY DEFINER``), que impone un mínimo de 365 días.
"""

from __future__ import annotations

from sqlalchemy import text

from app.core.db import get_sessionmaker

PURGE_BATCH_ROWS = 50_000


async def purge_expired(retention_days: int, *, batch_size: int = PURGE_BATCH_ROWS) -> int:
    """Borra por lotes las entradas más antiguas que la retención. Devuelve cuántas.

    Cada lote es una transacción corta, para no bloquear la tabla con un DELETE
    enorme.
    """
    total = 0
    while True:
        async with get_sessionmaker()() as session:
            result = await session.execute(
                text("SELECT purge_audit_log(:days, :batch)"),
                {"days": retention_days, "batch": batch_size},
            )
            deleted = int(result.scalar_one())
            await session.commit()
        total += deleted
        if deleted < batch_size:
            return total
