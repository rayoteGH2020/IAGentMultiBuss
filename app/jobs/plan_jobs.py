"""Tareas programadas de planes (D027)."""

from __future__ import annotations

from typing import Any

import structlog

from app.core.db import session_scope
from app.services import plan_change_service

log = structlog.get_logger(__name__)


async def apply_scheduled_plan_changes(ctx: dict[str, Any]) -> int:
    """Persiste los cambios de plan programados cuya fecha ha llegado (cron ARQ).

    El plan nuevo ya rige por lectura desde las 00:00 del día 1
    (``entitlement_service.resolve_plan_code_for_tenant``); este job lo guarda
    en ``tenants.plan_code`` con historial y auditoría.
    """
    async with session_scope() as db:
        applied = await plan_change_service.apply_due_plan_changes(db)
    log.info("plan.scheduled_changes_applied", applied=applied)
    return applied
