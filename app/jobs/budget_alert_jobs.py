"""Job ARQ: emails del presupuesto mensual de IA (admin al 80 %, SADM al 90 %, corte del chat)."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.core.db import session_factory_for_worker
from app.services import llm_budget_alert_service

logger = structlog.get_logger(__name__)


async def send_llm_budget_alert(ctx: dict[str, Any], tenant_id: str, kind: str) -> dict[str, Any]:
    """Envía el aviso; la frecuencia ya la limitó quien encoló el job."""
    _ = ctx
    tenant_uuid = uuid.UUID(tenant_id)
    async with session_factory_for_worker(tenant_uuid) as db:
        if kind == "sadm_cutoff":
            sent = await llm_budget_alert_service.send_sadm_alert(db, tenant_uuid)
            return {"status": "sent" if sent else "no_sadm_email", "kind": kind}
        if kind in ("budget_warning", "chat_cutoff"):
            admin_kind: llm_budget_alert_service.AdminAlertKind = (
                "budget_warning" if kind == "budget_warning" else "chat_cutoff"
            )
            sent = await llm_budget_alert_service.send_admin_alert(db, tenant_uuid, admin_kind)
            return {"status": "sent" if sent else "no_admin", "kind": kind}
    logger.warning("worker.llm_budget_alert.unknown_kind", tenant_id=tenant_id, kind=kind)
    return {"status": "skipped", "reason": "unknown_kind"}
