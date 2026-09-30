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
    FEATURE_UI_LABELS,
    LIMIT_CODES,
    LIMIT_UI_LABELS,
    OVERRIDE_SETTINGS_KEY,
    normalize_plan_code,
    plan_ui_name,
)
from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.models.plan import PlanEntitlement
from app.models.tenant import Tenant
from app.schemas.entitlements import (
    Entitlements,
    EntitlementsOverride,
    PlanLimitItem,
    PlanSummary,
    QuotaUsage,
)
from app.services import plan_change_service, plan_service

log = get_logger(__name__)


def _format_limit(value: Decimal | int | None) -> str:
    """``None`` → "Ilimitado"; enteros con separador de miles español (1.500)."""
    if value is None:
        return "Ilimitado"
    return f"{int(value):,}".replace(",", ".")


def _usage_percent(used: int, cap: int | None) -> int | None:
    """% consumido (0-100); None si ilimitado. Se satura en 100 si hubo override a la baja."""
    if cap is None or cap <= 0:
        return None
    return min(100, round(used * 100 / cap))


def _limit_item(
    code: str, label: str, ents: Entitlements, usage: QuotaUsage | None
) -> PlanLimitItem:
    if usage is None:
        return PlanLimitItem(label=label, value=_format_limit(ents.limit(code)))
    return PlanLimitItem(
        label=label,
        value=_format_limit(usage.cap),
        used=usage.used,
        percent=_usage_percent(usage.used, usage.cap),
    )


def build_plan_summary(
    ents: Entitlements,
    usage: dict[str, QuotaUsage] | None = None,
) -> PlanSummary:
    """Plan efectivo del tenant para el cliente: funciones, límites y consumo.

    Usa las capacidades ya resueltas (catálogo + override SADM + kill-switch),
    así el cliente ve lo que realmente tiene y no el catálogo genérico. Con
    ``usage`` (plan_quota_service.get_limit_usage) el tope mostrado es el que
    se aplica y se añade el consumo. Los límites a 0 no se listan: esa
    prestación no está incluida.
    """
    usage = usage or {}
    features = [label for code, label in FEATURE_UI_LABELS.items() if ents.has(code)]
    limits = [
        _limit_item(code, label, ents, usage.get(code))
        for code, label in LIMIT_UI_LABELS.items()
        if (usage[code].cap if code in usage else ents.limit(code)) != 0
    ]
    return PlanSummary(
        code=ents.plan_code,
        name=plan_ui_name(ents.plan_code),
        features=features,
        limits=limits,
    )


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
    """Plan efectivo: cambio programado ya vencido (D027) o ``plan_code`` (legacy ``plan``)."""
    due = plan_change_service.due_scheduled_plan_code(tenant)
    if due is not None:
        return normalize_plan_code(due)
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
