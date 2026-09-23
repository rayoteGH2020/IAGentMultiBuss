from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from markupsafe import escape
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.core.templating import render
from app.deps import CurrentTenant, CurrentUser, RequireAdmin, get_db
from app.services import entitlement_service, plan_quota_service, stripe_billing_service


def _billing_error_fragment(message: str) -> HTMLResponse:
    safe = escape(message)
    return HTMLResponse(
        content=(
            '<p class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 '
            f'text-sm text-red-800">{safe}</p>'
        ),
        status_code=400,
    )


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
    purchasable = await stripe_billing_service.list_purchasable_plans(db)
    return render(
        request,
        full="pages/settings/billing.html",
        ctx={
            "user": user,
            "tenant": tenant,
            "usage": usage,
            "ents": ents,
            "stripe_configured": stripe_billing_service.is_stripe_configured(),
            "purchasable_plans": purchasable,
            "checkout": request.query_params.get("checkout"),
        },
    )


def _hx_redirect(url: str) -> Response:
    return Response(
        status_code=200,
        headers={"HX-Redirect": url},
        media_type="text/plain",
        content=b"",
    )


@router.post("/billing/checkout")
async def settings_billing_checkout(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    _admin: RequireAdmin,
    plan_code: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        url = await stripe_billing_service.create_checkout_session(
            db,
            tenant=tenant,
            plan_code=plan_code,
            actor_user_id=user.id,
            actor_email=user.email,
        )
    except ValidationError as exc:
        if request.headers.get("HX-Request") == "true":
            return _billing_error_fragment(exc.message)
        ents = await entitlement_service.resolve_entitlements(db, tenant)
        usage = await plan_quota_service.get_usage_snapshot(db, ents, tenant.id)
        purchasable = await stripe_billing_service.list_purchasable_plans(db)
        return render(
            request,
            full="pages/settings/billing.html",
            ctx={
                "user": user,
                "tenant": tenant,
                "usage": usage,
                "ents": ents,
                "stripe_configured": stripe_billing_service.is_stripe_configured(),
                "purchasable_plans": purchasable,
                "billing_error": exc.message,
            },
            status_code=400,
        )
    if request.headers.get("HX-Request") == "true":
        return _hx_redirect(url)
    return RedirectResponse(url=url, status_code=303)


@router.post("/billing/portal")
async def settings_billing_portal(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    _admin: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        url = await stripe_billing_service.create_billing_portal_session(db, tenant=tenant)
    except ValidationError as exc:
        if request.headers.get("HX-Request") == "true":
            return _billing_error_fragment(exc.message)
        ents = await entitlement_service.resolve_entitlements(db, tenant)
        usage = await plan_quota_service.get_usage_snapshot(db, ents, tenant.id)
        purchasable = await stripe_billing_service.list_purchasable_plans(db)
        return render(
            request,
            full="pages/settings/billing.html",
            ctx={
                "user": user,
                "tenant": tenant,
                "usage": usage,
                "ents": ents,
                "stripe_configured": stripe_billing_service.is_stripe_configured(),
                "purchasable_plans": purchasable,
                "billing_error": exc.message,
            },
            status_code=400,
        )
    if request.headers.get("HX-Request") == "true":
        return _hx_redirect(url)
    return RedirectResponse(url=url, status_code=303)
