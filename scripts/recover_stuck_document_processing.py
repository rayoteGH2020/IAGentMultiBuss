"""Desbloquea un documento stuck en status=processing (attempt huérfano).

Tras matar el worker a mitad de un job, la fila y ``document_processing_attempts``
pueden quedar en ``processing`` sin job ARQ. Este script:

1. Marca el documento y el attempt abierto como ``failed`` (``processing_interrupted``).
2. Opcionalmente resetea el semáforo Redis ``invoice:extract:active:{tenant_id}``.

Uso:
  infisical run -- uv run python scripts/recover_stuck_document_processing.py \\
    --tenant-id <uuid> --kind invoice --document-id <uuid> [--reset-slot]
"""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

import structlog
from app.core.cache import get_redis
from app.core.db import session_scope, set_tenant_context
from app.core.document_processing_errors import DocumentErrorCode
from app.jobs.invoice_slots import slot_key_for_tenant
from app.services import document_processing_service

log = structlog.get_logger(__name__)

_KINDS = ("invoice", "ticket", "contract", "insurance")


async def _run(*, tenant_id: UUID, kind: str, document_id: UUID, reset_slot: bool) -> None:
    async with session_scope() as db:
        await set_tenant_context(db, str(tenant_id))
        await document_processing_service.abandon_stale_processing(
            db,
            tenant_id=tenant_id,
            document_kind=kind,  # type: ignore[arg-type]
            document_id=document_id,
            force=True,
        )
        await db.commit()

    if reset_slot:
        redis = get_redis()
        key = slot_key_for_tenant(tenant_id)
        deleted = await redis.delete(key)
        log.info("recover_stuck.slot_reset", key=key, deleted=deleted)

    log.info(
        "recover_stuck.done",
        tenant_id=str(tenant_id),
        document_kind=kind,
        document_id=str(document_id),
        error_code=DocumentErrorCode.processing_interrupted.value,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--kind", choices=_KINDS, required=True)
    parser.add_argument("--document-id", type=UUID, required=True)
    parser.add_argument(
        "--reset-slot",
        action="store_true",
        help="DEL invoice:extract:active:{tenant_id} en Redis",
    )
    args = parser.parse_args()
    asyncio.run(
        _run(
            tenant_id=args.tenant_id,
            kind=args.kind,
            document_id=args.document_id,
            reset_slot=args.reset_slot,
        ),
    )


if __name__ == "__main__":
    main()
