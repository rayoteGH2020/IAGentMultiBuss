"""Servicio de catalogo de planes (global, sin RLS)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.core.db import set_tenant_context
from app.core.entitlement_codes import (
    ENTITLEMENT_KIND_FEATURE,
    ENTITLEMENT_KIND_LIMIT,
    OVERRIDE_SETTINGS_KEY,
    PLAN_CODES,
    PLAN_FEATURES,
    PLAN_LIMITS,
    PLAN_META,
    normalize_plan_code,
)
from app.core.errors import NotFoundError, ValidationError
from app.models.plan import Plan, PlanEntitlement
from app.models.tenant import Tenant
from app.models.tenant_plan_change import TenantPlanChange
from app.schemas.entitlements import EntitlementsOverride, PlanRead
from app.services import audit_service

ACTION_PLAN_ASSIGNED = "sadm.plan_assigned"
ACTION_ENTITLEMENTS_OVERRIDE = "sadm.entitlements_override"
RESOURCE_TENANT = "tenant"

SOURCE_SADM = "sadm"
SOURCE_STRIPE = "stripe"


async def list_plans(
    db: AsyncSession,
    *,
    active_only: bool = True,
) -> list[PlanRead]:
    stmt = select(Plan).order_by(Plan.sort_order.asc(), Plan.code.asc())
    if active_only:
        stmt = stmt.where(Plan.is_active.is_(True))
    result = await db.execute(stmt)
    return [PlanRead.model_validate(row) for row in result.scalars().all()]


async def get_plan_by_code(db: AsyncSession, code: str) -> Plan | None:
    result = await db.execute(
        select(Plan).where(Plan.code == code).options(selectinload(Plan.entitlements))
    )
    return result.scalar_one_or_none()


async def require_plan_by_code(db: AsyncSession, code: str) -> Plan:
    plan = await get_plan_by_code(db, code)
    if plan is None or not plan.is_active:
        raise NotFoundError(f"Plan '{code}' not found")
    return plan


def _entitlement_rows_for_plan(plan_id: UUID, plan_code: str) -> list[PlanEntitlement]:
    rows: list[PlanEntitlement] = []
    for feature in sorted(PLAN_FEATURES[plan_code]):
        rows.append(
            PlanEntitlement(
                id=uuid4(),
                plan_id=plan_id,
                kind=ENTITLEMENT_KIND_FEATURE,
                code=feature,
                enabled=True,
                limit_value=None,
            )
        )
    for limit_code, value in PLAN_LIMITS[plan_code].items():
        rows.append(
            PlanEntitlement(
                id=uuid4(),
                plan_id=plan_id,
                kind=ENTITLEMENT_KIND_LIMIT,
                code=limit_code,
                enabled=None,
                limit_value=value,
            )
        )
    return rows


async def seed_plan_catalog(db: AsyncSession, *, replace_missing: bool = True) -> list[Plan]:
    """Idempotente: asegura planes activos del catalogo y resincroniza entitlements.

    - Crea ``basic`` / ``advanced`` / ``premium`` si faltan.
    - Actualiza nombre/descripcion/sort y reemplaza filas de entitlements.
    - Desactiva codigos legacy (``medium``, ``high``, ``total``) si existen.
    """
    existing = await db.execute(select(Plan))
    by_code = {plan.code: plan for plan in existing.scalars().all()}
    touched: list[Plan] = []

    for code in sorted(PLAN_CODES, key=lambda c: PLAN_META[c][2]):
        name, description, sort_order = PLAN_META[code]
        plan = by_code.get(code)
        if plan is None:
            if not replace_missing:
                continue
            plan = Plan(
                id=uuid4(),
                code=code,
                name=name,
                description=description,
                sort_order=sort_order,
                is_active=True,
                is_public=True,
            )
            db.add(plan)
            await db.flush()
            by_code[code] = plan
        else:
            plan.name = name
            plan.description = description
            plan.sort_order = sort_order
            plan.is_active = True
            plan.is_public = True
            await db.execute(delete(PlanEntitlement).where(PlanEntitlement.plan_id == plan.id))
        for row in _entitlement_rows_for_plan(plan.id, code):
            db.add(row)
        touched.append(plan)

    for legacy_code in ("medium", "high", "total"):
        legacy = by_code.get(legacy_code)
        if legacy is not None:
            legacy.is_active = False
            legacy.is_public = False

    await db.flush()
    return touched


def catalog_limits_for(plan_code: str) -> dict[str, Decimal | None]:
    """Copia de limites canonicos (sin BD). Util en tests unitarios."""
    return dict(PLAN_LIMITS[plan_code])


def catalog_features_for(plan_code: str) -> frozenset[str]:
    return PLAN_FEATURES[plan_code]


async def _require_tenant(db: AsyncSession, tenant_id: UUID) -> Tenant:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise NotFoundError(f"Tenant {tenant_id} not found")
    return tenant


async def get_plan_by_stripe_price_id(db: AsyncSession, stripe_price_id: str) -> Plan | None:
    cleaned = stripe_price_id.strip()
    if not cleaned:
        return None
    result = await db.execute(
        select(Plan).where(Plan.stripe_price_id == cleaned).options(selectinload(Plan.entitlements))
    )
    return result.scalar_one_or_none()


async def assign_tenant_plan(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    plan_code: str,
    actor_user_id: UUID | None,
    reason: str | None = None,
    source: str = SOURCE_SADM,
    metadata: dict[str, Any] | None = None,
) -> Tenant:
    """Asigna ``plan_code`` al tenant. Punto unico para SADM y Stripe (Paso09)."""
    code = normalize_plan_code(plan_code)
    if code not in PLAN_CODES:
        raise ValidationError(f"Plan code '{plan_code}' is not valid")
    plan = await require_plan_by_code(db, code)
    tenant = await _require_tenant(db, tenant_id)
    from_code = tenant.plan_code or tenant.plan
    plan_changed = from_code != plan.code
    if plan_changed:
        tenant.plan_code = plan.code
        tenant.plan = plan.code
        await db.flush()
    await set_tenant_context(db, str(tenant.id))
    if plan_changed:
        db.add(
            TenantPlanChange(
                tenant_id=tenant.id,
                from_plan_code=from_code,
                to_plan_code=plan.code,
                changed_by_user_id=actor_user_id,
                reason=reason,
                metadata_={
                    "source": source,
                    **(metadata or {}),
                },
            )
        )
        await audit_service.log_action(
            db,
            tenant_id=tenant.id,
            user_id=actor_user_id,
            action=ACTION_PLAN_ASSIGNED,
            resource_type=RESOURCE_TENANT,
            resource_id=tenant.id,
            metadata={
                "from_plan_code": from_code,
                "to_plan_code": plan.code,
                "reason": reason,
                "source": source,
                **(metadata or {}),
            },
        )
    return tenant


def tenant_override_raw(tenant: Tenant) -> dict[str, Any] | None:
    settings = tenant.settings if isinstance(tenant.settings, dict) else {}
    raw = settings.get(OVERRIDE_SETTINGS_KEY)
    return raw if isinstance(raw, dict) else None


async def set_tenant_entitlements_override(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    override: EntitlementsOverride | None,
    actor_user_id: UUID | None,
    reason: str | None = None,
) -> Tenant:
    """Escribe o limpia ``tenants.settings.entitlements_override`` (SADM)."""
    tenant = await _require_tenant(db, tenant_id)
    settings: dict[str, Any] = dict(tenant.settings) if isinstance(tenant.settings, dict) else {}
    previous = settings.get(OVERRIDE_SETTINGS_KEY)
    if override is None or (not override.features and not override.limits):
        settings.pop(OVERRIDE_SETTINGS_KEY, None)
        new_payload: dict[str, Any] | None = None
    else:
        new_payload = override.model_dump(mode="json")
        settings[OVERRIDE_SETTINGS_KEY] = new_payload
    tenant.settings = settings
    flag_modified(tenant, "settings")
    await db.flush()
    await set_tenant_context(db, str(tenant.id))
    await audit_service.log_action(
        db,
        tenant_id=tenant.id,
        user_id=actor_user_id,
        action=ACTION_ENTITLEMENTS_OVERRIDE,
        resource_type=RESOURCE_TENANT,
        resource_id=tenant.id,
        metadata={
            "previous": previous,
            "override": new_payload,
            "reason": reason,
        },
    )
    return tenant
