from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import render
from app.deps import CurrentTenant, CurrentUser, RequireAdmin, get_db
from app.services import tenant_service

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
    return render(request, full="pages/settings/profile.html", ctx={"user": user, "tenant": tenant})


@router.get("/organization")
async def settings_organization(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
) -> HTMLResponse:
    return render(
        request,
        full="pages/settings/organization.html",
        ctx={"user": user, "tenant": tenant, "saved": False},
    )


@router.get("/billing")
async def settings_billing(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
) -> HTMLResponse:
    return render(request, full="pages/settings/billing.html", ctx={"user": user, "tenant": tenant})


@router.post("/organization/name")
async def update_organization_name(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    _: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    name: str = Form(..., min_length=2, max_length=120),
) -> HTMLResponse:
    await tenant_service.update_tenant_display_name(db, tenant, name)
    return render(
        request,
        full="components/tenant_info.html",
        partial="components/tenant_info.html",
        ctx={"user": user, "tenant": tenant, "saved": True},
    )
