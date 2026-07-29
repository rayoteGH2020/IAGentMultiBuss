"""Jobs ARQ: procesado de pólizas de seguro subidas."""

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
from app.llm.extraction import extract_insurance
from app.services import document_processing_service, insurance_service, processing_charge_service

logger = structlog.get_logger(__name__)


async def process_insurance(
    ctx: dict[str, Any],
    insurance_id: str,
    tenant_id: str,
    max_pdf_pages: int | None = None,
) -> dict[str, Any]:
    """Descarga fichero desde R2, extrae con LLM y guarda en `Insurance`."""
    insurance_uuid = uuid.UUID(insurance_id)
    tenant_uuid = uuid.UUID(tenant_id)
    logger.info("worker.insurance.start", insurance_id=insurance_id, tenant_id=tenant_id)

    redis_conn = ctx.get("redis")
    if redis_conn is None:
        redis_conn = get_redis()

    async with (
        tenant_invoice_extraction_slot(redis_conn, tenant_uuid),
        session_factory_for_worker(tenant_uuid) as db,
    ):
        insurance_row = await insurance_service.get_insurance(db, tenant_uuid, insurance_uuid)

        await document_processing_service.begin_processing_attempt(
            db,
            tenant_id=tenant_uuid,
            document_kind="insurance",
            document_id=insurance_uuid,
        )

        if not insurance_row.source_file_key:
            await insurance_service.mark_failed(
                db,
                insurance_id=insurance_uuid,
                tenant_id=tenant_uuid,
                error="missing source_file_key",
            )
            await db.commit()
            return {"status": "failed", "insurance_id": insurance_id}

        try:
            storage = get_storage()
            file_bytes = await storage.download_bytes(insurance_row.source_file_key)
            mime = insurance_row.source_mime or "application/pdf"

            extraction = await extract_insurance(
                file_bytes=file_bytes,
                mime_type=mime,
                tenant_id=tenant_uuid,
                db=db,
                source_filename=insurance_row.source_filename,
                max_pdf_pages=max_pdf_pages,
            )

            await insurance_service.apply_extraction_result(
                db,
                insurance=insurance_row,
                data=extraction.insurance,
                llm_call_id=extraction.llm_call_id,
            )
            await processing_charge_service.settle_charge(
                db,
                tenant_id=tenant_uuid,
                document_kind="insurance",
                document_id=insurance_uuid,
                llm_call_id=extraction.llm_call_id,
            )
            await db.commit()
            logger.info(
                "worker.insurance.done",
                insurance_id=insurance_id,
                aseguradora=extraction.insurance.aseguradora,
                llm_call_id=str(extraction.llm_call_id),
            )
            return {"status": "ok", "insurance_id": insurance_id}

        except MediaLimitExceeded as exc:
            await db.rollback()
            await set_tenant_context(db, str(tenant_uuid))
            await insurance_service.mark_failed(
                db,
                insurance_id=insurance_uuid,
                tenant_id=tenant_uuid,
                error=exc.message,
                error_code=exc.error_code,
                detail=exc.detail,
            )
            await db.commit()
            logger.warning(
                "worker.insurance.rejected_by_limits",
                insurance_id=insurance_id,
                tenant_id=tenant_id,
                error_code=exc.error_code.value,
            )
            return {"status": "rejected", "insurance_id": insurance_id}

        except LLMCompleteError as exc:
            await db.commit()
            await set_tenant_context(db, str(tenant_uuid))
            await insurance_service.mark_failed(
                db,
                insurance_id=insurance_uuid,
                tenant_id=tenant_uuid,
                error=str(exc.message)[:500],
                llm_call_id=exc.llm_call_id,
            )
            await db.commit()
            logger.exception(
                "worker.insurance.failed",
                insurance_id=insurance_id,
                llm_call_id=str(exc.llm_call_id),
            )
            return {"status": "failed", "insurance_id": insurance_id}

        except Exception as exc:
            await db.rollback()
            await set_tenant_context(db, str(tenant_uuid))
            await insurance_service.mark_failed(
                db,
                insurance_id=insurance_uuid,
                tenant_id=tenant_uuid,
                error=str(exc)[:500],
            )
            await db.commit()
            logger.exception("worker.insurance.failed", insurance_id=insurance_id)
            return {"status": "failed", "insurance_id": insurance_id}
