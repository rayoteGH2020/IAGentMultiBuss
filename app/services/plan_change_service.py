"""Cambios de plan del SADM: primera asignación inmediata, el resto el día 1 siguiente (D027).

La facturación va por mes natural completo, así que un cambio de plan no puede
entrar a mitad de mes. El cambio pendiente vive en
``tenants.settings['scheduled_plan_change']``; entra en vigor por lectura en
cuanto llega su fecha (``due_scheduled_plan_code``) y un cron lo persiste con
historial y auditoría (``apply_due_plan_changes``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.billing_period import current_period_start, local_date, next_period_start
from app.core.db import set_tenant_context
from app.core.entitlement_codes import PLAN_CODES, SCHEDULED_PLAN_CHANGE_KEY, normalize_plan_code
from app.core.errors import NotFoundError, ValidationError
from app.models.tenant import Tenant
from app.models.tenant_plan_change import TenantPlanChange
from app.schemas.entitlements import ScheduledPlanChange
from app.services import audit_service, plan_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

ACTION_PLAN_CHANGE_SCHEDULED = "sadm.plan_change_scheduled"
ACTION_PLAN_CHANGE_CANCELLED = "sadm.plan_change_cancelled"
SOURCE_SCHEDULED = "scheduled"


@dataclass(frozen=True)
class PlanChangeResult:
    """Resultado de una petición de cambio de plan del SADM."""

    tenant: Tenant
    applied_now: bool
    effective_date: date | None


def scheduled_plan_change(tenant: Tenant) -> ScheduledPlanChange | None:
    """Cambio pendiente del tenant; ``None`` si no hay o el payload no es válido."""
    settings = tenant.settings if isinstance(tenant.settings, dict) else {}
    raw = settings.get(SCHEDULED_PLAN_CHANGE_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return ScheduledPlanChange.model_validate(raw)
    except PydanticValidationError:
        logger.warning("plan_change.scheduled_invalid", tenant_id=str(tenant.id))
        return None


def due_scheduled_plan_code(tenant: Tenant, *, today: date | None = None) -> str | None:
    """Plan del cambio pendiente si su fecha ya ha llegado (antes de que lo persista el cron)."""
    change = scheduled_plan_change(tenant)
    if change is None or change.effective_date > (today or local_date()):
        return None
    return change.plan_code


def _persisted_plan_code(tenant: Tenant) -> str:
    """Plan guardado del tenant (sin tener en cuenta el cambio pendiente)."""
    return normalize_plan_code(tenant.plan_code or tenant.plan)


async def _require_tenant(db: AsyncSession, tenant_id: UUID) -> Tenant:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    if tenant is None:
        raise NotFoundError(f"Tenant {tenant_id} not found")
    return tenant


async def _has_plan_history(db: AsyncSession, tenant_id: UUID) -> bool:
    await set_tenant_context(db, str(tenant_id))
    row = await db.execute(
        select(TenantPlanChange.id).where(TenantPlanChange.tenant_id == tenant_id).limit(1)
    )
    return row.scalar_one_or_none() is not None


def _write_scheduled(tenant: Tenant, payload: dict[str, Any] | None) -> None:
    settings: dict[str, Any] = dict(tenant.settings) if isinstance(tenant.settings, dict) else {}
    if payload is None:
        settings.pop(SCHEDULED_PLAN_CHANGE_KEY, None)
    else:
        settings[SCHEDULED_PLAN_CHANGE_KEY] = payload
    tenant.settings = settings
    flag_modified(tenant, "settings")


async def _audit(
    db: AsyncSession,
    tenant: Tenant,
    actor_user_id: UUID | None,
    action: str,
    metadata: dict[str, Any],
) -> None:
    await audit_service.log_action(
        db,
        tenant_id=tenant.id,
        user_id=actor_user_id,
        action=action,
        resource_type=plan_service.RESOURCE_TENANT,
        resource_id=tenant.id,
        metadata=metadata,
    )


async def request_plan_change(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    plan_code: str,
    actor_user_id: UUID | None,
    reason: str | None = None,
) -> PlanChangeResult:
    """Primera asignación: inmediata. Después: programada para el día 1 del mes siguiente.

    Pedir el plan que ya tiene el tenant anula el cambio pendiente, si lo hay.
    """
    code = normalize_plan_code(plan_code)
    if code not in PLAN_CODES:
        raise ValidationError(f"Plan code '{plan_code}' is not valid")
    tenant = await _require_tenant(db, tenant_id)

    if not await _has_plan_history(db, tenant.id):
        tenant = await plan_service.assign_tenant_plan(
            db,
            tenant_id=tenant.id,
            plan_code=code,
            actor_user_id=actor_user_id,
            reason=reason,
            metadata={"initial_assignment": True},
            force_record=True,
        )
        return PlanChangeResult(tenant=tenant, applied_now=True, effective_date=None)

    if code == _persisted_plan_code(tenant):
        await cancel_scheduled_plan_change(db, tenant_id=tenant.id, actor_user_id=actor_user_id)
        return PlanChangeResult(tenant=tenant, applied_now=False, effective_date=None)

    effective = next_period_start(current_period_start())
    change = ScheduledPlanChange(
        plan_code=code,
        effective_date=effective,
        requested_by=actor_user_id,
        requested_at=datetime.now(UTC),
        reason=reason,
    )
    _write_scheduled(tenant, change.model_dump(mode="json"))
    await db.flush()
    await _audit(
        db,
        tenant,
        actor_user_id,
        ACTION_PLAN_CHANGE_SCHEDULED,
        {
            "from_plan_code": _persisted_plan_code(tenant),
            "to_plan_code": code,
            "effective_date": effective.isoformat(),
            "reason": reason,
        },
    )
    return PlanChangeResult(tenant=tenant, applied_now=False, effective_date=effective)


async def cancel_scheduled_plan_change(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    actor_user_id: UUID | None,
) -> Tenant:
    """Anula el cambio pendiente (no-op si no hay)."""
    tenant = await _require_tenant(db, tenant_id)
    change = scheduled_plan_change(tenant)
    if change is None:
        return tenant
    _write_scheduled(tenant, None)
    await db.flush()
    await set_tenant_context(db, str(tenant.id))
    await _audit(
        db,
        tenant,
        actor_user_id,
        ACTION_PLAN_CHANGE_CANCELLED,
        {"to_plan_code": change.plan_code, "effective_date": change.effective_date.isoformat()},
    )
    return tenant


async def apply_due_plan_changes(db: AsyncSession, *, today: date | None = None) -> int:
    """Persiste los cambios de plan cuya fecha ha llegado (tarea programada)."""
    day = today or local_date()
    tenants = (
        (await db.execute(select(Tenant).where(Tenant.settings.has_key(SCHEDULED_PLAN_CHANGE_KEY))))
        .scalars()
        .all()
    )
    applied = 0
    for tenant in tenants:
        change = scheduled_plan_change(tenant)
        if change is None or change.effective_date > day:
            continue
        await plan_service.assign_tenant_plan(
            db,
            tenant_id=tenant.id,
            plan_code=change.plan_code,
            actor_user_id=change.requested_by,
            reason=change.reason,
            source=SOURCE_SCHEDULED,
            metadata={"effective_date": change.effective_date.isoformat()},
        )
        _write_scheduled(tenant, None)
        await db.flush()
        applied += 1
    return applied
