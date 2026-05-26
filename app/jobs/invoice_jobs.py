"""Jobs ARQ: procesado de facturas subidas.

Flujo completo de un job:
  1. Adquirir slot de concurrencia por tenant (semáforo Redis).
  2. Abrir sesión de BD con contexto de tenant (RLS activo).
  3. Leer la factura de BD para obtener la key de R2.
  4. Descargar el fichero de R2.
  5. Llamar al LLM (extract_invoice → LLMCall guardado en sesión).
  6. Persistir el resultado estructurado (Invoice + InvoiceLines).
  7. Commit único que persiste Invoice, InvoiceLines y LLMCall de forma atómica.
  En caso de error: rollback → re-establecer contexto de tenant → mark_failed → commit.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.core.cache import get_redis
from app.core.db import session_factory_for_worker, set_tenant_context
from app.core.errors import LLMCompleteError
from app.core.storage import get_storage
from app.jobs.invoice_slots import tenant_invoice_extraction_slot
from app.llm.extraction import extract_invoice
from app.services import invoice_service

logger = structlog.get_logger(__name__)


async def process_invoice(ctx: dict[str, Any], invoice_id: str, tenant_id: str) -> dict[str, Any]:
    """Descarga fichero desde R2, extrae con LLM y guarda en `Invoice` / `InvoiceLine`."""
    # Los argumentos llegan como str porque ARQ serializa a JSON; UUID no es
    # serializable directamente, se convirtieron a str en enqueue_invoice_processing.
    inv_uuid = uuid.UUID(invoice_id)
    t_uuid = uuid.UUID(tenant_id)
    logger.info("worker.invoice.start", invoice_id=invoice_id, tenant_id=tenant_id)

    # ARQ inyecta su conexión Redis interna en ctx["redis"]. En tests donde
    # ctx es un dict vacío (sin ARQ real), se usa el cliente Redis de la app
    # como fallback para que el semáforo de slots funcione igualmente.
    redis_conn = ctx.get("redis")
    if redis_conn is None:
        redis_conn = get_redis()

    async with (
        # Orden de los context managers importa:
        # 1. tenant_invoice_extraction_slot: si el tenant está al límite de
        #    concurrencia, lanza arq.worker.Retry ANTES de abrir sesión de BD,
        #    evitando conexiones innecesarias a Postgres.
        # 2. session_factory_for_worker: abre la sesión de BD con el contexto
        #    de tenant (SET LOCAL app.current_tenant = ...) para que RLS aplique.
        tenant_invoice_extraction_slot(redis_conn, t_uuid),
        session_factory_for_worker(t_uuid) as db,
    ):
        # Re-leer la factura desde BD aunque el route la creó momentos antes:
        # el worker corre en un proceso separado sin estado compartido con la app.
        invoice_row = await invoice_service.get_invoice(db, t_uuid, inv_uuid)

        if not invoice_row.source_file_key:
            # Caso de integridad: el fichero nunca llegó a R2 (bug o condición
            # de carrera). Se marca como failed con un mensaje claro en lugar
            # de fallar con un AttributeError críptico más adelante.
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
            # Fallback a PDF si por algún motivo el MIME no se guardó; es el
            # tipo más probable en el contexto de facturas.
            mime = invoice_row.source_mime or "application/pdf"

            # extract_invoice llama al LLM y añade un LLMCall a la sesión db
            # pero NO hace commit; la transacción permanece abierta hasta el
            # commit explícito al final del bloque try. Así LLMCall, Invoice
            # e InvoiceLines se persisten de forma atómica en un solo commit.
            extraction = await extract_invoice(
                file_bytes=file_bytes,
                mime_type=mime,
                tenant_id=t_uuid,
                db=db,
                source_filename=invoice_row.source_filename,
            )

            await invoice_service.apply_extraction_result(
                db,
                invoice=invoice_row,
                factura=extraction.factura,
                llm_call_id=extraction.llm_call_id,
            )
            # Commit único: persiste en una sola transacción el Invoice
            # actualizado, las InvoiceLines nuevas y el LLMCall de la extracción.
            # Si cualquier INSERT falla, todo se revierte y el job se marca failed.
            await db.commit()
            logger.info(
                "worker.invoice.done",
                invoice_id=invoice_id,
                proveedor=extraction.factura.proveedor,
                total=str(extraction.factura.total),
                llm_call_id=str(extraction.llm_call_id),
            )
            return {"status": "ok", "invoice_id": invoice_id}

        except LLMCompleteError as exc:
            # La fila `llm_calls` ya está en sesión tras el flush en complete().
            # Commit parcial para no perder métricas de tokens/coste en fallos LLM.
            await db.commit()
            await set_tenant_context(db, str(t_uuid))
            await invoice_service.mark_failed(
                db,
                invoice_id=inv_uuid,
                tenant_id=t_uuid,
                error=str(exc.message)[:500],
                llm_call_id=exc.llm_call_id,
            )
            await db.commit()
            logger.exception(
                "worker.invoice.failed",
                invoice_id=invoice_id,
                llm_call_id=str(exc.llm_call_id),
            )
            return {"status": "failed", "invoice_id": invoice_id}

        except Exception as exc:
            # Rollback: deshace cualquier escritura parcial de la extracción
            # (LLMCall añadido a la sesión, líneas parcialmente insertadas, etc.).
            await db.rollback()
            # CRÍTICO: rollback en Postgres revierte también los SET LOCAL,
            # incluyendo `app.current_tenant`. Sin re-establecer el contexto de
            # tenant, la siguiente escritura (mark_failed) fallaría con RLS
            # porque la política tenant_isolation ve NULL en current_setting().
            await set_tenant_context(db, str(t_uuid))
            await invoice_service.mark_failed(
                db,
                invoice_id=inv_uuid,
                tenant_id=t_uuid,
                error=str(exc)[:500],
            )
            await db.commit()
            # logger.exception incluye el traceback completo en el log (a
            # diferencia de logger.error que solo incluye el mensaje). Esencial
            # para diagnosticar fallos del LLM o de R2 desde los logs.
            logger.exception("worker.invoice.failed", invoice_id=invoice_id)
            # Se devuelve dict en lugar de re-lanzar la excepción: si se
            # relanzase, ARQ marcaría el job como fallido y lo reintentaría
            # (hasta max_tries). Devolviendo {"status": "failed"} el job se
            # considera completado (aunque con error), y la UI lee el estado
            # de la BD (Invoice.status = failed) en lugar del resultado ARQ.
            return {"status": "failed", "invoice_id": invoice_id}
