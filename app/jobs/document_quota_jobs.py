"""Jobs ARQ del cupo de facturas y tickets (bloque 2, spec planes §4.2)."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select

from app.core.db import session_factory_for_worker, session_scope
from app.jobs.queue import enqueue_invoice_processing, enqueue_ticket_processing
from app.models import Tenant
from app.services import document_quota_service

logger = structlog.get_logger(__name__)


async def _process_tenant(tenant_id: uuid.UUID) -> int:
    """Saca de ``quota_pending`` lo que quepa y lo encola tras el commit."""
    async with session_factory_for_worker(tenant_id) as db:
        released = await document_quota_service.process_pending(db, tenant_id)
        await db.commit()
    for item in released:
        if item.kind == "invoice":
            await enqueue_invoice_processing(item.document_id, item.tenant_id)
        else:
            await enqueue_ticket_processing(item.document_id, item.tenant_id)
    return len(released)


async def process_quota_pending(
    ctx: dict[str, Any], tenant_id: str | None = None
) -> dict[str, Any]:
    """Procesa documentos pendientes de cupo.

    Con ``tenant_id``: tras una ampliación del SADM o una reserva devuelta. Sin él
    (cron horario): todos los tenants, lo que cubre la renovación del día 1.
    """
    _ = ctx
    if tenant_id is not None:
        released = await _process_tenant(uuid.UUID(tenant_id))
        return {"status": "ok", "released_count": released}

    async with session_scope() as db:
        tenant_ids = list((await db.execute(select(Tenant.id))).scalars().all())
    released = 0
    for tid in tenant_ids:
        try:
            released += await _process_tenant(tid)
        except Exception:
            # Un tenant con problemas no bloquea al resto; queda para la siguiente hora.
            logger.exception("worker.quota_pending.tenant_failed", tenant_id=str(tid))
    return {"status": "ok", "released_count": released, "tenant_count": len(tenant_ids)}


async def send_documents_quota_alert(
    ctx: dict[str, Any], tenant_id: str, kind: str
) -> dict[str, Any]:
    """Email al admin: 80 % de la bolsa o primer documento pendiente del mes."""
    _ = ctx
    if kind not in ("documents_warning", "documents_exhausted"):
        logger.warning("worker.documents_quota_alert.unknown_kind", tenant_id=tenant_id, kind=kind)
        return {"status": "skipped", "reason": "unknown_kind"}
    alert_kind: document_quota_service.QuotaAlertKind = (
        "documents_warning" if kind == "documents_warning" else "documents_exhausted"
    )
    tenant_uuid = uuid.UUID(tenant_id)
    async with session_factory_for_worker(tenant_uuid) as db:
        sent = await document_quota_service.send_alert(db, tenant_uuid, alert_kind)
    return {"status": "sent" if sent else "no_admin", "kind": kind}
