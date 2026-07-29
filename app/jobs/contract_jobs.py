"""Jobs ARQ: procesado de contratos subidos."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.core.cache import get_redis
from app.core.db import session_factory_for_worker, set_tenant_context
from app.core.errors import LLMCompleteError
from app.core.media_limits import MediaLimitExceeded
from app.core.storage import get_storage
from app.jobs.invoice_slots import tenant_invoice_extraction_slot
from app.llm.extraction import extract_contract
from app.services import contract_service, document_processing_service, processing_charge_service

logger = structlog.get_logger(__name__)


async def process_contract(
    ctx: dict[str, Any],
    contract_id: str,
    tenant_id: str,
    max_pdf_pages: int | None = None,
) -> dict[str, Any]:
    """Descarga fichero desde R2, extrae con LLM y guarda en `Contract`."""
    contract_uuid = uuid.UUID(contract_id)
    tenant_uuid = uuid.UUID(tenant_id)
    logger.info("worker.contract.start", contract_id=contract_id, tenant_id=tenant_id)

    redis_conn = ctx.get("redis")
    if redis_conn is None:
        redis_conn = get_redis()

    async with (
        tenant_invoice_extraction_slot(redis_conn, tenant_uuid),
        session_factory_for_worker(tenant_uuid) as db,
    ):
        contract_row = await contract_service.get_contract(db, tenant_uuid, contract_uuid)

        await document_processing_service.begin_processing_attempt(
            db,
            tenant_id=tenant_uuid,
            document_kind="contract",
            document_id=contract_uuid,
        )

        if not contract_row.source_file_key:
            await contract_service.mark_failed(
                db,
                contract_id=contract_uuid,
                tenant_id=tenant_uuid,
                error="missing source_file_key",
            )
            await db.commit()
            return {"status": "failed", "contract_id": contract_id}

        try:
            storage = get_storage()
            file_bytes = await storage.download_bytes(contract_row.source_file_key)
            mime = contract_row.source_mime or "application/pdf"

            extraction = await extract_contract(
                file_bytes=file_bytes,
                mime_type=mime,
                tenant_id=tenant_uuid,
                db=db,
                source_filename=contract_row.source_filename,
                max_pdf_pages=max_pdf_pages,
            )

            await contract_service.apply_extraction_result(
                db,
                contract=contract_row,
                data=extraction.contract,
                llm_call_id=extraction.llm_call_id,
            )
            await processing_charge_service.settle_charge(
                db,
                tenant_id=tenant_uuid,
                document_kind="contract",
                document_id=contract_uuid,
                llm_call_id=extraction.llm_call_id,
            )
            await db.commit()
            logger.info(
                "worker.contract.done",
                contract_id=contract_id,
                parte_contraria=extraction.contract.parte_contraria,
                llm_call_id=str(extraction.llm_call_id),
            )
            return {"status": "ok", "contract_id": contract_id}

        except MediaLimitExceeded as exc:
            await db.rollback()
            await set_tenant_context(db, str(tenant_uuid))
            await contract_service.mark_failed(
                db,
                contract_id=contract_uuid,
                tenant_id=tenant_uuid,
                error=exc.message,
                error_code=exc.error_code,
                detail=exc.detail,
            )
            await db.commit()
            logger.warning(
                "worker.contract.rejected_by_limits",
                contract_id=contract_id,
                tenant_id=tenant_id,
                error_code=exc.error_code.value,
            )
            return {"status": "rejected", "contract_id": contract_id}

        except LLMCompleteError as exc:
            await db.commit()
            await set_tenant_context(db, str(tenant_uuid))
            await contract_service.mark_failed(
                db,
                contract_id=contract_uuid,
                tenant_id=tenant_uuid,
                error=str(exc.message)[:500],
                llm_call_id=exc.llm_call_id,
            )
            await db.commit()
            logger.exception(
                "worker.contract.failed",
                contract_id=contract_id,
                llm_call_id=str(exc.llm_call_id),
            )
            return {"status": "failed", "contract_id": contract_id}

        except Exception as exc:
            await db.rollback()
            await set_tenant_context(db, str(tenant_uuid))
            await contract_service.mark_failed(
                db,
                contract_id=contract_uuid,
                tenant_id=tenant_uuid,
                error=str(exc)[:500],
            )
            await db.commit()
            logger.exception("worker.contract.failed", contract_id=contract_id)
            return {"status": "failed", "contract_id": contract_id}
