"""Job ARQ: email al admin por el presupuesto mensual de IA (80 % y corte del chat)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import structlog

from app.core.db import session_factory_for_worker
from app.services import llm_budget_alert_service

if TYPE_CHECKING:
    from app.services.llm_budget_alert_service import AlertKind

logger = structlog.get_logger(__name__)

_KINDS: frozenset[str] = frozenset({"budget_warning", "chat_cutoff"})


async def send_llm_budget_alert(ctx: dict[str, Any], tenant_id: str, kind: str) -> dict[str, Any]:
    """Envía el aviso; la frecuencia ya la limitó quien encoló el job."""
    _ = ctx
    if kind not in _KINDS:
        logger.warning("worker.llm_budget_alert.unknown_kind", tenant_id=tenant_id, kind=kind)
        return {"status": "skipped", "reason": "unknown_kind"}
    alert_kind: AlertKind = "budget_warning" if kind == "budget_warning" else "chat_cutoff"
    tenant_uuid = uuid.UUID(tenant_id)
    async with session_factory_for_worker(tenant_uuid) as db:
        sent = await llm_budget_alert_service.send_admin_alert(db, tenant_uuid, alert_kind)
    return {"status": "sent" if sent else "no_admin", "kind": kind}
