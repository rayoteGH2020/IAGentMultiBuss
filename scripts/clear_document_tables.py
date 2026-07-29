"""Vacía todas las tablas de dominio dejando identidad multi-tenant intacta.

Conserva: users, tenants, memberships, alembic_version, professional_working_hours, doc_types
No elimina el esquema ni los ficheros en R2 (source_file_key / knowledge keys).

Uso: infisical run -- uv run python scripts/clear_document_tables.py

Bloqueado si APP_ENV=production.
"""

from __future__ import annotations

import asyncio
import sys

import structlog
from app.config import get_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

log = structlog.get_logger(__name__)

# Tablas de identidad / migraciones: NUNCA truncar.
PRESERVED_TABLES = frozenset(
    {
        "users",
        "tenants",
        "memberships",
        "alembic_version",
        "professional_working_hours",
        "doc_types",
    }
)

# Hijas antes que padres cuando sea posible; CASCADE cubre FKs cruzadas.
TABLES: tuple[str, ...] = (
    # Documentos / tickets / cargos
    "invoice_lines",
    "document_processing_attempts",
    "processing_charges",
    "invoices",
    "tickets",
    # Chat interno
    "chat_messages",
    "chat_threads",
    # Knowledge
    "knowledge_chunks",
    "knowledge_documents",
    # Canales externos
    "channel_messages",
    "conversations",
    "channel_response_cache",
    "channel_integrations",
    # Calendario / scheduling
    "appointments",
    "professional_specialties",
    "professionals",
    "schedule_exceptions",
    "services",
    "business_hours",
    "calendar_integrations",
    # Observabilidad / métricas
    "llm_calls",
    "audit_log",
    "usage_meter",
)


def _count_sql(tables: tuple[str, ...]) -> str:
    parts = [
        f"SELECT '{name}' AS table_name, COUNT(*)::int AS row_count FROM {name}"  # noqa: S608
        for name in tables
    ]
    return " UNION ALL ".join(parts)


async def main() -> None:
    settings = get_settings()
    if settings.app_env == "production":
        log.error("refusing_to_truncate_in_production")
        sys.exit(1)

    overlap = PRESERVED_TABLES.intersection(TABLES)
    if overlap:
        log.error("refusing_to_truncate_preserved_tables", tables=sorted(overlap))
        sys.exit(1)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    truncate_sql = text(f"TRUNCATE TABLE {', '.join(TABLES)} RESTART IDENTITY CASCADE")

    try:
        async with engine.begin() as conn:
            await conn.execute(truncate_sql)

        async with engine.connect() as conn:
            result = await conn.execute(text(_count_sql(TABLES)))
            counts = {row.table_name: row.row_count for row in result}

        log.info(
            "domain_tables_truncated",
            tables=list(TABLES),
            preserved=sorted(PRESERVED_TABLES),
            counts=counts,
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
