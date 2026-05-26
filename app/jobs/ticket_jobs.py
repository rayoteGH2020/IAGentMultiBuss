"""Jobs ARQ: procesado de tickets subidos."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.core.cache import get_redis
from app.core.db import session_factory_for_worker, set_tenant_context
from app.core.errors import LLMCompleteError
from app.core.storage import get_storage
from app.jobs.invoice_slots import tenant_invoice_extraction_slot
from app.llm.extraction import extract_ticket
from app.services import ticket_service

logger = structlog.get_logger(__name__)


async def process_ticket(ctx: dict[str, Any], ticket_id: str, tenant_id: str) -> dict[str, Any]:
    """Descarga fichero desde R2, extrae con LLM y guarda en `Ticket`."""
    ticket_uuid = uuid.UUID(ticket_id)
    tenant_uuid = uuid.UUID(tenant_id)
    logger.info("worker.ticket.start", ticket_id=ticket_id, tenant_id=tenant_id)

    redis_conn = ctx.get("redis")
    if redis_conn is None:
        redis_conn = get_redis()

    async with (
        tenant_invoice_extraction_slot(redis_conn, tenant_uuid),
        session_factory_for_worker(tenant_uuid) as db,
    ):
        ticket_row = await ticket_service.get_ticket(db, tenant_uuid, ticket_uuid)

        if not ticket_row.source_file_key:
            await ticket_service.mark_failed(
                db,
                ticket_id=ticket_uuid,
                tenant_id=tenant_uuid,
                error="missing source_file_key",
            )
            await db.commit()
            return {"status": "failed", "ticket_id": ticket_id}

        try:
            storage = get_storage()
            file_bytes = await storage.download_bytes(ticket_row.source_file_key)
            mime = ticket_row.source_mime or "application/pdf"

            extraction = await extract_ticket(
                file_bytes=file_bytes,
                mime_type=mime,
                tenant_id=tenant_uuid,
                db=db,
                source_filename=ticket_row.source_filename,
            )

            await ticket_service.apply_extraction_result(
                db,
                ticket=ticket_row,
                recibo=extraction.ticket,
                llm_call_id=extraction.llm_call_id,
            )
            await db.commit()
            logger.info(
                "worker.ticket.done",
                ticket_id=ticket_id,
                comercio=extraction.ticket.comercio,
                total=str(extraction.ticket.total),
                llm_call_id=str(extraction.llm_call_id),
            )
            return {"status": "ok", "ticket_id": ticket_id}

        except LLMCompleteError as exc:
            await db.commit()
            await set_tenant_context(db, str(tenant_uuid))
            await ticket_service.mark_failed(
                db,
                ticket_id=ticket_uuid,
                tenant_id=tenant_uuid,
                error=str(exc.message)[:500],
                llm_call_id=exc.llm_call_id,
            )
            await db.commit()
            logger.exception(
                "worker.ticket.failed",
                ticket_id=ticket_id,
                llm_call_id=str(exc.llm_call_id),
            )
            return {"status": "failed", "ticket_id": ticket_id}

        except Exception as exc:
            await db.rollback()
            await set_tenant_context(db, str(tenant_uuid))
            await ticket_service.mark_failed(
                db,
                ticket_id=ticket_uuid,
                tenant_id=tenant_uuid,
                error=str(exc)[:500],
            )
            await db.commit()
            logger.exception("worker.ticket.failed", ticket_id=ticket_id)
            return {"status": "failed", "ticket_id": ticket_id}
