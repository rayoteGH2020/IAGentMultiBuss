"""Seed idempotente del catálogo global doc_types.

Inserta factura/ticket/contrato/seguro si faltan. No modifica filas existentes
ni reactiva tipos desactivados. Seguro de reejecutar.

Uso: infisical run -- uv run python scripts/seed_doc_types.py
"""

from __future__ import annotations

import asyncio

import structlog
from app.core.db import session_scope
from app.services import doc_type_service

log = structlog.get_logger(__name__)


async def main() -> None:
    async with session_scope() as db:
        result = await doc_type_service.ensure_default_doc_types(db)

    log.info(
        "doc_types_seed_completed",
        inserted=list(result.inserted),
        skipped=list(result.skipped),
    )


if __name__ == "__main__":
    asyncio.run(main())
