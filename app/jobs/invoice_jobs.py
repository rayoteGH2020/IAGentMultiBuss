"""Jobs ARQ: procesado de facturas subidas."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.core.cache import get_redis
from app.core.db import session_factory_for_worker, set_tenant_context
from app.core.storage import get_storage
from app.jobs.invoice_slots import tenant_invoice_extraction_slot
from app.llm.extraction import extract_invoice
from app.services import invoice_service

logger = structlog.get_logger(__name__)


async def process_invoice(ctx: dict[str, Any], invoice_id: str, tenant_id: str) -> dict[str, Any]:
    """Descarga fichero desde R2, extrae con LLM y guarda en `Invoice` / `InvoiceLine`."""
    inv_uuid = uuid.UUID(invoice_id)
    t_uuid = uuid.UUID(tenant_id)
    logger.info("worker.invoice.start", invoice_id=invoice_id, tenant_id=tenant_id)

    redis_conn = ctx.get("redis")
    if redis_conn is None:
        redis_conn = get_redis()

    async with (
        tenant_invoice_extraction_slot(redis_conn, t_uuid),
        session_factory_for_worker(t_uuid) as db,
    ):
        invoice_row = await invoice_service.get_invoice(db, t_uuid, inv_uuid)

        if not invoice_row.source_file_key:
            await invoice_service.mark_failed(
                db,
                invoice_id=inv_uuid,
                tenant_id=t_uuid,
                error="missing source_file_key",
            )
            await db.commit()
            return {"status": "failed", "invoice_id": invoice_id}

        try:
            storage = get_storage()
            file_bytes = await storage.download_bytes(invoice_row.source_file_key)
            mime = invoice_row.source_mime or "application/pdf"

            factura = await extract_invoice(
                file_bytes=file_bytes,
                mime_type=mime,
                tenant_id=t_uuid,
                db=db,
            )

            await invoice_service.apply_extraction_result(
                db,
                invoice=invoice_row,
                factura=factura,
            )
            await db.commit()
            logger.info(
                "worker.invoice.done",
                invoice_id=invoice_id,
                proveedor=factura.proveedor,
                total=str(factura.total),
            )
            return {"status": "ok", "invoice_id": invoice_id}

        except Exception as exc:
            await db.rollback()
            await set_tenant_context(db, str(t_uuid))
            await invoice_service.mark_failed(
                db,
                invoice_id=inv_uuid,
                tenant_id=t_uuid,
                error=str(exc)[:500],
            )
            await db.commit()
            logger.exception("worker.invoice.failed", invoice_id=invoice_id)
            return {"status": "failed", "invoice_id": invoice_id}
