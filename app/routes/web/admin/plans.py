"""SADM — catalogo de planes y asignacion a tenants (Paso05)."""

from __future__ import annotations

import json
from typing import Annotated
from uuid import UUID  # noqa: TC003

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from app.core.errors import ValidationError
from app.core.templating import render
from app.deps import CurrentUser, SuperAdmin, get_db_no_tenant
from app.schemas.entitlements import EntitlementsOverride
from app.services import admin_service, plan_service

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/sadm/plans", tags=["sadm"])


async def _plans_page_ctx(db: AsyncSession) -> dict[str, object]:
    plans = await plan_service.list_plans(db, active_only=True)
    tenants = await admin_service.list_all_tenants(db)
    return {"plans": plans, "tenants": tenants}


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
    plans = await plan_service.list_plans(db, active_only=True)
    override = plan_service.tenant_override_raw(tenant)
    override_json = json.dumps(override, indent=2, ensure_ascii=False) if override else ""
    return render(
        request,
        full="pages/sadm/plans/tenant.html",
        partial="pages/sadm/plans/_tenant_form.html",
        ctx={
            "tenant": tenant,
            "plans": plans,
            "override_json": override_json,
        },
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
    tenant = await plan_service.assign_tenant_plan(
        db,
        tenant_id=tenant_id,
        plan_code=plan_code.strip(),
        actor_user_id=user.id,
        reason=reason.strip() or None,
    )
    plans = await plan_service.list_plans(db, active_only=True)
    override = plan_service.tenant_override_raw(tenant)
    override_json = json.dumps(override, indent=2, ensure_ascii=False) if override else ""
    return render(
        request,
        full="pages/sadm/plans/tenant.html",
        partial="pages/sadm/plans/_tenant_form.html",
        ctx={
            "tenant": tenant,
            "plans": plans,
            "override_json": override_json,
            "notice": f"Plan actualizado a «{tenant.plan_code}».",
        },
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
    plans = await plan_service.list_plans(db, active_only=True)
    stored = plan_service.tenant_override_raw(tenant)
    stored_json = json.dumps(stored, indent=2, ensure_ascii=False) if stored else ""
    return render(
        request,
        full="pages/sadm/plans/tenant.html",
        partial="pages/sadm/plans/_tenant_form.html",
        ctx={
            "tenant": tenant,
            "plans": plans,
            "override_json": stored_json,
            "notice": "Override limpiado." if stored is None else "Override guardado.",
        },
    )
