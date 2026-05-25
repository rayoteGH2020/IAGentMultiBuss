"""Vacía las tablas de documentos y observabilidad LLM del módulo 1.

Uso: infisical run -- uv run python scripts/clear_document_tables.py

No elimina el esquema ni los ficheros en R2 (source_file_key).
"""

from __future__ import annotations

import asyncio
import sys

import structlog
from app.config import get_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

log = structlog.get_logger(__name__)

TABLES = ("invoice_lines", "invoices", "tickets", "llm_calls")


async def main() -> None:
    settings = get_settings()
    if settings.app_env == "production":
        log.error("refusing_to_truncate_in_production")
        sys.exit(1)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    truncate_sql = text(f"TRUNCATE TABLE {', '.join(TABLES)} RESTART IDENTITY")

    try:
        async with engine.begin() as conn:
            await conn.execute(truncate_sql)

        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT 'invoice_lines' AS table_name, COUNT(*)::int AS row_count
                    FROM invoice_lines
                    UNION ALL
                    SELECT 'invoices', COUNT(*)::int FROM invoices
                    UNION ALL
                    SELECT 'tickets', COUNT(*)::int FROM tickets
                    UNION ALL
                    SELECT 'llm_calls', COUNT(*)::int FROM llm_calls
                    """
                )
            )
            counts = {row.table_name: row.row_count for row in result}

        log.info("document_tables_truncated", tables=list(TABLES), counts=counts)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
