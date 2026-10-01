"""SADM — catalogo de planes y asignacion a tenants (Paso05, D027)."""

from __future__ import annotations

import json
from typing import Annotated, Any
from uuid import UUID  # noqa: TC003

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from app.core.billing_period import current_period_start, period_end
from app.core.entitlement_codes import MONTHLY_QUOTA_UI_LABELS, plan_ui_name
from app.core.errors import ValidationError
from app.core.templating import render
from app.deps import CurrentUser, SuperAdmin, get_db_no_tenant
from app.schemas.entitlements import EntitlementsOverride
from app.services import (
    admin_service,
    document_quota_service,
    entitlement_service,
    monthly_quota_service,
    plan_change_service,
    plan_service,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/sadm/plans", tags=["sadm"])

_TENANT_FULL = "pages/sadm/plans/tenant.html"
_TENANT_PARTIAL = "pages/sadm/plans/_tenant_form.html"


async def _plans_page_ctx(db: AsyncSession) -> dict[str, object]:
    plans = await plan_service.list_plans(db, active_only=True)
    tenants = await admin_service.list_all_tenants(db)
    return {"plans": plans, "tenants": tenants}


async def _tenant_ctx(
    db: AsyncSession, tenant: Any, *, notice: str | None = None
) -> dict[str, object]:
    # ``tenant`` llega de los services; sin tipo de modelo (routes no importa models).
    override = plan_service.tenant_override_raw(tenant)
    ents = await entitlement_service.resolve_entitlements(db, tenant)
    period = current_period_start()
    return {
        "tenant": tenant,
        "plans": await plan_service.list_plans(db, active_only=True),
        "override_json": json.dumps(override, indent=2, ensure_ascii=False) if override else "",
        "scheduled_change": plan_change_service.scheduled_plan_change(tenant),
        "monthly_quotas": list(
            (await monthly_quota_service.get_usage(db, ents, tenant.id, period=period)).values()
        ),
        "quota_labels": MONTHLY_QUOTA_UI_LABELS,
        "period_start": period,
        "period_end": period_end(period),
        "notice": notice,
    }


@router.get("", response_class=HTMLResponse)
async def plans_index(
    request: Request,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    ctx = await _plans_page_ctx(db)
    return render(
        request,
        full="pages/sadm/plans/index.html",
        partial="pages/sadm/plans/_tenants.html",
        ctx=ctx,
    )


@router.get("/tenants/{tenant_id}", response_class=HTMLResponse)
async def tenant_plan_detail(
    request: Request,
    tenant_id: UUID,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    tenant = await admin_service.get_tenant(db, tenant_id)
    return render(
        request,
        full=_TENANT_FULL,
        partial=_TENANT_PARTIAL,
        ctx=await _tenant_ctx(db, tenant),
    )


@router.post("/tenants/{tenant_id}", response_class=HTMLResponse)
async def assign_plan(
    request: Request,
    tenant_id: UUID,
    user: CurrentUser,
    _admin: SuperAdmin,
    plan_code: Annotated[str, Form()],
    reason: Annotated[str, Form()] = "",
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    result = await plan_change_service.request_plan_change(
        db,
        tenant_id=tenant_id,
        plan_code=plan_code.strip(),
        actor_user_id=user.id,
        reason=reason.strip() or None,
    )
    if result.applied_now:
        notice = f"Plan asignado: «{plan_ui_name(result.tenant.plan_code)}»."
    elif result.effective_date is not None:
        notice = (
            f"Cambio a «{plan_ui_name(plan_code)}» programado para el "
            f"{result.effective_date:%d/%m/%Y}."
        )
    else:
        notice = "El tenant ya tiene ese plan; no queda ningún cambio pendiente."
    return render(
        request,
        full=_TENANT_FULL,
        partial=_TENANT_PARTIAL,
        ctx=await _tenant_ctx(db, result.tenant, notice=notice),
    )


@router.post("/tenants/{tenant_id}/scheduled/cancel", response_class=HTMLResponse)
async def cancel_scheduled_change(
    request: Request,
    tenant_id: UUID,
    user: CurrentUser,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    tenant = await plan_change_service.cancel_scheduled_plan_change(
        db, tenant_id=tenant_id, actor_user_id=user.id
    )
    return render(
        request,
        full=_TENANT_FULL,
        partial=_TENANT_PARTIAL,
        ctx=await _tenant_ctx(db, tenant, notice="Cambio de plan programado anulado."),
    )


@router.post("/tenants/{tenant_id}/quota-extra", response_class=HTMLResponse)
async def add_quota_extra(
    request: Request,
    tenant_id: UUID,
    user: CurrentUser,
    _admin: SuperAdmin,
    code: Annotated[str, Form()],
    amount: Annotated[int, Form()],
    reason: Annotated[str, Form()] = "",
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    tenant = await admin_service.get_tenant(db, tenant_id)
    total = await monthly_quota_service.add_extra(
        db,
        tenant_id=tenant.id,
        code=code.strip(),
        amount=amount,
        actor_user_id=user.id,
        reason=reason.strip() or None,
    )
    await document_quota_service.on_quota_extra_added(db, tenant_id=tenant.id, code=code.strip())
    label = MONTHLY_QUOTA_UI_LABELS.get(code, code)
    return render(
        request,
        full=_TENANT_FULL,
        partial=_TENANT_PARTIAL,
        ctx=await _tenant_ctx(
            db, tenant, notice=f"«{label}» ampliado en {amount} este mes (extra total: {total})."
        ),
    )


@router.post("/tenants/{tenant_id}/override", response_class=HTMLResponse)
async def set_override(
    request: Request,
    tenant_id: UUID,
    user: CurrentUser,
    _admin: SuperAdmin,
    override_json: Annotated[str, Form()] = "",
    clear: Annotated[str, Form()] = "",
    reason: Annotated[str, Form()] = "",
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    override: EntitlementsOverride | None = None
    if clear.strip().lower() in {"1", "true", "on", "yes"}:
        override = None
    else:
        raw = override_json.strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValidationError("Override JSON invalido") from exc
            try:
                override = EntitlementsOverride.model_validate(parsed)
            except PydanticValidationError as exc:
                raise ValidationError(
                    "Override no valido",
                    details={"errors": exc.errors()},
                ) from exc
        else:
            override = None

    tenant = await plan_service.set_tenant_entitlements_override(
        db,
        tenant_id=tenant_id,
        override=override,
        actor_user_id=user.id,
        reason=reason.strip() or None,
    )
    stored = plan_service.tenant_override_raw(tenant)
    return render(
        request,
        full=_TENANT_FULL,
        partial=_TENANT_PARTIAL,
        ctx=await _tenant_ctx(
            db, tenant, notice="Override limpiado." if stored is None else "Override guardado."
        ),
    )
