"""Resolucion unica de entitlements por tenant (Paso02).

Prioridad:
1. Kill-switch global (Settings.entitlements_disabled_features).
2. Override en tenants.settings['entitlements_override'].
3. Catalogo del plan.
4. Fail-closed si el plan no existe o esta inactivo.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.entitlement_codes import (
    ENTITLEMENT_KIND_FEATURE,
    ENTITLEMENT_KIND_LIMIT,
    FEATURE_CODES,
    LIMIT_CODES,
    OVERRIDE_SETTINGS_KEY,
    normalize_plan_code,
)
from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.models.plan import PlanEntitlement
from app.models.tenant import Tenant
from app.schemas.entitlements import Entitlements, EntitlementsOverride
from app.services import plan_service

log = get_logger(__name__)


def fail_closed_entitlements(plan_code: str) -> Entitlements:
    """Sin features y limites a 0 (deny total)."""
    return Entitlements(
        plan_code=plan_code,
        features=frozenset(),
        limits={code: Decimal("0") for code in sorted(LIMIT_CODES)},
        fail_closed=True,
    )


def entitlements_from_rows(
    plan_code: str,
    rows: list[PlanEntitlement],
) -> Entitlements:
    features: set[str] = set()
    limits: dict[str, Decimal | None] = {code: Decimal("0") for code in LIMIT_CODES}

    for row in rows:
        if row.kind == ENTITLEMENT_KIND_FEATURE:
            if row.code in FEATURE_CODES and row.enabled is True:
                features.add(row.code)
        elif row.kind == ENTITLEMENT_KIND_LIMIT and row.code in LIMIT_CODES:
            # null declarado en BD = unlimited; ausencia de fila ya es 0.
            limits[row.code] = row.limit_value

    return Entitlements(
        plan_code=plan_code,
        features=frozenset(features),
        limits=limits,
        fail_closed=False,
    )


def parse_override(raw: object) -> EntitlementsOverride:
    """Valida override; codigos desconocidos -> ValidationError."""
    if raw is None:
        return EntitlementsOverride()
    if not isinstance(raw, dict):
        raise ValidationError(
            "entitlements_override must be an object",
            details={"key": OVERRIDE_SETTINGS_KEY},
        )
    try:
        return EntitlementsOverride.model_validate(raw)
    except PydanticValidationError as exc:
        raise ValidationError(
            "Invalid entitlements_override",
            details={"errors": exc.errors()},
        ) from exc


def apply_override(base: Entitlements, override: EntitlementsOverride) -> Entitlements:
    """Merge override sobre catalogo: puede reducir o ampliar features/limites conocidos."""
    features = set(base.features)
    limits = dict(base.limits)

    for code, enabled in override.features.items():
        if enabled:
            features.add(code)
        else:
            features.discard(code)

    for code, value in override.limits.items():
        limits[code] = value

    return Entitlements(
        plan_code=base.plan_code,
        features=frozenset(features),
        limits=limits,
        fail_closed=False,
    )


def apply_kill_switch(base: Entitlements, disabled_features: list[str]) -> Entitlements:
    if not disabled_features:
        return base
    disabled = {code for code in disabled_features if code in FEATURE_CODES}
    if not disabled:
        return base
    return Entitlements(
        plan_code=base.plan_code,
        features=frozenset(code for code in base.features if code not in disabled),
        limits=dict(base.limits),
        fail_closed=base.fail_closed,
    )


def resolve_plan_code_for_tenant(tenant: Tenant) -> str:
    """Prefer ``plan_code``; cae a ``plan`` legacy (free -> basic)."""
    raw = getattr(tenant, "plan_code", None) or tenant.plan
    return normalize_plan_code(raw)


async def resolve_entitlements(
    db: AsyncSession,
    tenant: Tenant,
    *,
    settings: Settings | None = None,
) -> Entitlements:
    """Punto unico de resolucion. No cachea aqui: el caller puede guardar en request.state."""
    cfg = settings or get_settings()
    plan_code = resolve_plan_code_for_tenant(tenant)
    plan = await plan_service.get_plan_by_code(db, plan_code)
    if plan is None or not plan.is_active:
        log.warning(
            "entitlements_fail_closed",
            tenant_id=str(tenant.id),
            plan_code=plan_code,
            reason="plan_missing_or_inactive",
        )
        return fail_closed_entitlements(plan_code)

    base = entitlements_from_rows(plan_code, list(plan.entitlements))
    override_raw: Any = None
    if isinstance(tenant.settings, dict):
        override_raw = tenant.settings.get(OVERRIDE_SETTINGS_KEY)
    override = parse_override(override_raw)
    merged = apply_override(base, override)
    return apply_kill_switch(merged, list(cfg.entitlements_disabled_features))


async def resolve_tenant(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    settings: Settings | None = None,
) -> Entitlements:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        log.warning("entitlements_fail_closed", tenant_id=str(tenant_id), reason="tenant_missing")
        return fail_closed_entitlements(normalize_plan_code(None))
    return await resolve_entitlements(db, tenant, settings=settings)


async def ensure_feature(
    db: AsyncSession,
    tenant_id: UUID,
    feature: str,
    *,
    settings: Settings | None = None,
) -> bool:
    """True si el tenant tiene la feature; pensado para workers/webhooks."""
    ents = await resolve_tenant(db, tenant_id, settings=settings)
    return ents.has(feature)
