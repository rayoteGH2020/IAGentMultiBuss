from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.clerk_frontend import clerk_browser_script_url
from app.core.errors import AppError, AuthError, ForbiddenError, RateLimitError
from app.core.session_cookies import clear_clerk_session_cookie
from app.core.templating import render
from app.deps import CurrentUser, RedisDep, get_db_no_tenant
from app.services import auth_service, onboarding_notify_service

router = APIRouter(tags=["auth"])

_NO_STORE_CACHE = {"Cache-Control": "no-store, max-age=0, must-revalidate"}

_NOTIFY_ERROR_MESSAGES: dict[str, str] = {
    "email_sadm_missing": ("Falta configurar EMAIL_SADM. Contacta al superadmin por otro canal."),
    "smtp_not_configured": (
        "Falta configurar SMTP en el servidor. Contacta al superadmin por otro canal."
    ),
    "missing_org_notify_send_failed": (
        "No se pudo enviar el email. Inténtalo de nuevo en unos minutos."
    ),
}


def _notify_error_message(exc: AppError) -> str:
    code = exc.details.get("code") if isinstance(exc.details, dict) else None
    if isinstance(code, str) and code in _NOTIFY_ERROR_MESSAGES:
        return _NOTIFY_ERROR_MESSAGES[code]
    return "No se pudo enviar el aviso. Inténtalo de nuevo."


def _extract_host(jwks_url: str) -> str:
    return urlparse(jwks_url).netloc


def _clerk_page_ctx(settings: Settings) -> dict[str, str]:
    frontend_host = _extract_host(settings.clerk_jwks_url)
    return {
        "clerk_pub_key": settings.clerk_publishable_key,
        "clerk_frontend_host": frontend_host,
        "clerk_js_script_url": clerk_browser_script_url(
            frontend_host,
            settings.clerk_js_version,
        ),
    }


def _cache_control_no_store(resp: Response) -> Response:
    for k, v in _NO_STORE_CACHE.items():
        resp.headers[k] = v
    return resp


@router.get("/login")
async def login_page(request: Request) -> Response:
    settings = get_settings()
    resp = render(
        request,
        full="pages/auth/login.html",
        ctx=_clerk_page_ctx(settings),
    )
    return _cache_control_no_store(resp)


@router.get("/signup")
async def signup_page(request: Request) -> Response:
    settings = get_settings()
    resp = render(
        request,
        full="pages/auth/signup.html",
        ctx=_clerk_page_ctx(settings),
    )
    return _cache_control_no_store(resp)


@router.get("/auth/organization")
async def organization_legacy_redirect() -> RedirectResponse:
    """Alias histórico: redirige a la pantalla de aviso sin organización."""
    return RedirectResponse(url="/onboarding", status_code=302)


@router.get("/onboarding")
async def onboarding_page(request: Request) -> Response:
    """Usuario autenticado en Clerk sin organización: aviso + notify al SADM.

    No se permite crear organizaciones desde el cliente; el alta es exclusiva
    del superadmin.
    """
    settings = get_settings()
    resp = render(
        request,
        full="pages/auth/no_org.html",
        ctx=_clerk_page_ctx(settings),
    )
    return _cache_control_no_store(resp)


@router.post("/onboarding/notify-superadmin", response_class=HTMLResponse)
async def notify_superadmin_missing_org(
    request: Request,
    redis: RedisDep,
) -> Response:
    """Envía al superadmin el aviso de usuario sin organización (HTMX)."""
    if not getattr(request.state, "auth_missing_organization", False):
        raise ForbiddenError("Organization already active")
    user = getattr(request.state, "user", None)
    if user is None:
        raise AuthError("Not authenticated")

    try:
        result = await onboarding_notify_service.notify_superadmin_missing_organization(
            user_email=user.email,
            user_id=user.id,
            redis=redis,
        )
    except RateLimitError:
        # Si ya se intentó (p. ej. fallo SMTP previo), ofrecer mailto si aplica.
        settings = get_settings()
        to = settings.email_sadm.strip()
        mailto_url = None
        if to and not settings.smtp_host.strip():
            mailto_url = onboarding_notify_service.build_missing_org_mailto_url(
                to=to,
                user_email=user.email,
            )
        resp = render(
            request,
            full="components/no_org_notified.html",
            partial="components/no_org_notified.html",
            ctx={"mailto_url": mailto_url},
        )
        return _cache_control_no_store(resp)
    except AppError as exc:
        # 200 a propósito: HTMX no hace swap en 4xx y el clic parece "no hacer nada".
        resp = render(
            request,
            full="components/no_org_notify_prompt.html",
            partial="components/no_org_notify_prompt.html",
            ctx={"notify_error": _notify_error_message(exc)},
            status_code=200,
        )
        return _cache_control_no_store(resp)

    resp = render(
        request,
        full="components/no_org_notified.html",
        partial="components/no_org_notified.html",
        ctx={"mailto_url": result.mailto_url},
    )
    return _cache_control_no_store(resp)


@router.get("/logout")
async def logout(request: Request) -> Response:
    """Cierra sesión en Clerk desde el navegador (necesario para borrar cookie __session HTTP-only)."""
    settings = get_settings()
    resp = render(
        request,
        full="pages/auth/sign_out.html",
        ctx=_clerk_page_ctx(settings),
    )
    return _cache_control_no_store(resp)


@router.get("/logout/done")
async def logout_done() -> RedirectResponse:
    """Limpieza server-side tras signOut del cliente por si quedaran cookies aplicables sólo desde el backend."""
    settings = get_settings()
    resp = RedirectResponse(url="/login", status_code=302)
    _cache_control_no_store(resp)
    clear_clerk_session_cookie(resp, settings)
    return resp


@router.get("/auth/change-password")
async def change_password_page(request: Request, user: CurrentUser) -> Response:
    """Primer login o cambio obligatorio tras alta por SADM."""
    if not getattr(user, "force_password_reset", False):
        return RedirectResponse(url="/", status_code=302)
    settings = get_settings()
    resp = render(
        request,
        full="pages/auth/change_password.html",
        ctx=_clerk_page_ctx(settings),
    )
    return _cache_control_no_store(resp)


@router.post("/auth/complete-password-reset")
async def complete_password_reset(
    user: CurrentUser,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> RedirectResponse:
    """Limpia el flag local tras cambio de contraseña en ClerkJS."""
    await auth_service.clear_force_password_reset(db, user.id)
    return RedirectResponse(url="/", status_code=302)
