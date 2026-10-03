"""Vuelve a extraer los contratos con ``contract_extraction_v2`` (P2b-3, migración p75).

La p75 elimina ``contracts.importe`` (era ambiguo) y añade cuota, periodicidad,
total, importe anual y fecha de firma. Los contratos ya procesados quedan sin
esos datos hasta volver a extraerlos. Pensado para **dev** (no hay producción):
cada contrato cuesta una llamada de extracción (≈0,004 €) que se registra en
``llm_calls`` y cuenta en el presupuesto de IA del tenant. Un contrato revisado
por el usuario vuelve a ``ready`` con los datos nuevos.

Uso:
  infisical run -- uv run python scripts/reextract_contracts.py --dry-run
  infisical run -- uv run python scripts/reextract_contracts.py [--tenant-id <uuid>]
"""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

import structlog
from app.core.db import session_factory_for_worker, session_scope
from app.core.storage import get_storage
from app.llm.extraction import extract_contract
from app.models import Contract, ContractStatus
from app.services import contract_service
from sqlalchemy import select

log = structlog.get_logger(__name__)

_REEXTRACT_STATUSES = (ContractStatus.ready, ContractStatus.reviewed)


async def _targets(tenant_id: UUID | None) -> list[tuple[UUID, UUID]]:
    """(tenant_id, contract_id) de los contratos procesados con fichero en storage."""
    async with session_scope() as db:
        stmt = select(Contract.tenant_id, Contract.id).where(
            Contract.status.in_(_REEXTRACT_STATUSES),
            Contract.source_file_key.is_not(None),
        )
        if tenant_id is not None:
            stmt = stmt.where(Contract.tenant_id == tenant_id)
        rows = (await db.execute(stmt)).all()
    return [(row.tenant_id, row.id) for row in rows]


async def _reextract(tenant_id: UUID, contract_id: UUID) -> bool:
    async with session_factory_for_worker(tenant_id) as db:
        contract = await contract_service.get_contract(db, tenant_id, contract_id)
        if not contract.source_file_key:
            return False
        file_bytes = await get_storage().download_bytes(contract.source_file_key)
        extraction = await extract_contract(
            file_bytes=file_bytes,
            mime_type=contract.source_mime or "application/pdf",
            tenant_id=tenant_id,
            db=db,
            source_filename=contract.source_filename,
        )
        await contract_service.apply_extraction_result(
            db,
            contract=contract,
            data=extraction.contract,
            llm_call_id=extraction.llm_call_id,
        )
        await db.commit()
        return contract.status == ContractStatus.ready


async def _run(*, tenant_id: UUID | None, dry_run: bool) -> None:
    targets = await _targets(tenant_id)
    log.info("reextract_contracts.targets", count=len(targets), dry_run=dry_run)
    if dry_run:
        return
    ok = failed = 0
    for target_tenant, contract_id in targets:
        try:
            if await _reextract(target_tenant, contract_id):
                ok += 1
            else:
                failed += 1
        except Exception as exc:  # un contrato fallido no debe parar el resto
            failed += 1
            log.warning(
                "reextract_contracts.failed",
                tenant_id=str(target_tenant),
                contract_id=str(contract_id),
                error_type=type(exc).__name__,
            )
    log.info("reextract_contracts.done", ok=ok, failed=failed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", type=UUID, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Solo cuenta los contratos")
    args = parser.parse_args()
    asyncio.run(_run(tenant_id=args.tenant_id, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
