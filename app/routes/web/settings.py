from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import render
from app.deps import CurrentTenant, CurrentUser, get_db
from app.services import entitlement_service, plan_quota_service

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def settings_redirect() -> RedirectResponse:
    return RedirectResponse(url="/settings/profile", status_code=302)


@router.get("/profile")
async def settings_profile(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
) -> HTMLResponse:
    return render(
        request,
        full="pages/settings/profile.html",
        ctx={"user": user, "tenant": tenant},
    )


@router.get("/organization")
async def settings_organization() -> RedirectResponse:
    # Los datos de organización se muestran ahora dentro de «Mi cuenta».
    return RedirectResponse(url="/settings/profile", status_code=302)


@router.get("/billing")
async def settings_billing(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    ents = await entitlement_service.resolve_entitlements(db, tenant)
    usage = await plan_quota_service.get_usage_snapshot(db, ents, tenant.id)
    return render(
        request,
        full="pages/settings/billing.html",
        ctx={"user": user, "tenant": tenant, "usage": usage, "ents": ents},
    )
