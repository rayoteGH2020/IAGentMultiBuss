import json
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from app.config import get_settings
from app.core.csrf import CSRF_HEADER_NAME, csrf_tenant_id_for_request, validate_csrf_token
from app.core.db import get_sessionmaker, set_tenant_context
from app.core.errors import AuthError
from app.core.permissions import (
    home_path_for_role,
    is_platform_superadmin,
    role_can_access_path,
)
from app.core.security import verify_clerk_jwt
from app.core.session_cookies import clear_clerk_session_cookie
from app.services.auth_service import (
    ensure_membership,
    org_id_from_claims,
    org_role_from_claims,
    resolve_tenant,
    resolve_user,
)

log = structlog.get_logger(__name__)

PUBLIC_PATHS = frozenset(
    {
        "/login",
        "/signup",
        "/auth/organization",
        "/auth/google/callback",
        "/health",
        "/health/db",
        "/health/redis",
        "/api/webhooks/clerk",
        "/api/webhooks/stripe",
    }
)
PUBLIC_PREFIXES = ("/static/", "/docs", "/redoc", "/openapi.json", "/openapi", "/demo")

SESSION_OPTIONAL_PATHS = frozenset(
    {"/login", "/signup", "/auth/organization", "/onboarding"},
)
# Acciones permitidas con JWT válido pero sin organización activa.
NO_ORG_ACTION_PATHS = frozenset({"/onboarding/notify-superadmin"})

CHANGE_PASSWORD_PATHS = frozenset({"/auth/change-password", "/auth/complete-password-reset"})
CSRF_PROTECTED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_EXEMPT_PATHS = frozenset(
    {
        "/api/webhooks/clerk",
        "/api/webhooks/whatsapp",
        "/api/webhooks/stripe",
    }
)
CSRF_EXEMPT_PREFIXES = ("/api/webhooks/telegram/",)


def _is_force_password_exempt(path: str) -> bool:
    """Rutas accesibles con force_password_reset activo (sin redirect)."""
    if path in CHANGE_PASSWORD_PATHS:
        return True
    if _is_logout_related_path(path):
        return True
    return _is_public(path)


def _is_logout_related_path(path: str) -> bool:
    return path == "/logout" or path.startswith("/logout/")


def _path_exempt_from_role_guard(path: str) -> bool:
    """Rutas que no aplican la matriz admin/member (auth, públicos, SADM, etc.)."""
    if _is_public(path) or _is_logout_related_path(path):
        return True
    if path in SESSION_OPTIONAL_PATHS or path in NO_ORG_ACTION_PATHS:
        return True
    if path in CHANGE_PASSWORD_PATHS:
        return True
    if path.startswith("/sadm") or path.startswith("/admin/"):
        return True
    if path.startswith("/metrics"):
        return True
    return path.startswith("/api/webhooks")


def _is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def _skip_session_resolution(path: str) -> bool:
    if path in SESSION_OPTIONAL_PATHS or path in NO_ORG_ACTION_PATHS:
        return False
    if _is_logout_related_path(path):
        return True
    return _is_public(path)


def _path_allowed_without_active_organization(path: str) -> bool:
    if path in SESSION_OPTIONAL_PATHS or path in NO_ORG_ACTION_PATHS:
        return True
    if _is_logout_related_path(path):
        return True
    return _is_public(path)


def _htmx_aware_redirect(request: Request, url: str) -> Response:
    """Redirect usable con hx-boost: HX-Redirect en lugar de 302 seguido por XHR.

    Si HTMX sigue un 302 y el destino no tiene el mismo hx-select (#app-frame),
    el swap deja la UI rota o vacía y el siguiente click parece "volver al inicio".
    """
    if request.headers.get("HX-Request") == "true":
        return Response(
            status_code=200,
            headers={"HX-Redirect": url},
            media_type="text/plain",
            content=b"",
        )
    return RedirectResponse(url=url, status_code=302)


def _login_redirect_clearing_session(request: Request) -> Response:
    """Fuerza re-login y borra ``__session`` (p. ej. JWT expirado)."""
    response = _htmx_aware_redirect(request, "/login")
    clear_clerk_session_cookie(response, get_settings())
    return response


def _csrf_exempt(path: str) -> bool:
    return path in CSRF_EXEMPT_PATHS or path.startswith(CSRF_EXEMPT_PREFIXES)


def _requires_csrf(request: Request) -> bool:
    if request.method.upper() not in CSRF_PROTECTED_METHODS:
        return False
    if _csrf_exempt(request.url.path):
        return False
    return not _skip_session_resolution(request.url.path)


def _csrf_error_response(request: Request) -> Response:
    if request.headers.get("HX-Request") == "true":
        return Response(
            status_code=403,
            media_type="application/json",
            content=b'{"code":"csrf_failed","message":"Invalid CSRF token"}',
        )
    return Response(status_code=403, content=b"Forbidden")


def _valid_csrf_request(request: Request) -> bool:
    user = getattr(request.state, "user", None)
    tenant = getattr(request.state, "tenant", None)
    if user is None:
        return False
    tenant_for_csrf = csrf_tenant_id_for_request(
        tenant_id=tenant.id if tenant is not None else None,
        missing_organization=bool(getattr(request.state, "auth_missing_organization", False)),
    )
    if tenant_for_csrf is None:
        return False
    token = request.headers.get(CSRF_HEADER_NAME, "")
    if not token:
        return False
    return validate_csrf_token(token, user_id=user.id, tenant_id=tenant_for_csrf)


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth.removeprefix("Bearer ").strip()
    cookie = request.cookies.get("__session")
    if isinstance(cookie, str) and cookie:
        return cookie
    return None


async def try_resolve_clerk_session(request: Request) -> None:
    """Rellena user/tenant/membership desde JWT, o marca auth_missing_organization."""
    request.state.auth_token_invalid = False
    request.state.auth_membership_revoked = False
    token = _extract_token(request)
    if not token:
        log.debug("auth.no_token", path=request.url.path)
        return
    try:
        claims = verify_clerk_jwt(token)
    except AuthError as e:
        log.warning("auth.jwt_invalid", path=request.url.path, error=str(e))
        request.state.auth_token_invalid = True
        return

    clerk_user_id = claims.get("sub")
    clerk_org_id = org_id_from_claims(claims)
    if not isinstance(clerk_user_id, str) or not clerk_user_id:
        request.state.auth_token_invalid = True
        return
    if not clerk_org_id:
        # JWT válido sin org: resolver usuario local para onboarding/notify
        # (email, CSRF) sin activar tenant/RLS.
        request.state.auth_missing_organization = True
        sm = get_sessionmaker()
        async with sm() as session:
            try:
                user = await resolve_user(session, clerk_user_id)
                await session.commit()
                request.state.user = user
            except Exception:
                await session.rollback()
                raise
        return

    sm = get_sessionmaker()
    async with sm() as session:
        try:
            user = await resolve_user(session, clerk_user_id)
            tenant = await resolve_tenant(session, clerk_org_id)
            await set_tenant_context(session, str(tenant.id))
            # JWT sincroniza rol solo si la membership sigue activa.
            # Tras organizationMembership.deleted (is_active=False) un JWT
            # obsoleto con org_id/admin NO debe recrear privilegios.
            membership = await ensure_membership(
                session,
                user.id,
                tenant.id,
                role=org_role_from_claims(claims),
            )
            if not membership.is_active:
                await session.commit()
                request.state.auth_membership_revoked = True
                log.warning(
                    "auth.membership_revoked",
                    clerk_user_id=clerk_user_id,
                    clerk_org_id=clerk_org_id,
                )
                return

            settings = get_settings()
            try:
                from app.core.errors import ValidationError
                from app.services import entitlement_service

                entitlements = await entitlement_service.resolve_entitlements(
                    session, tenant, settings=settings
                )
            except ValidationError:
                from app.services import entitlement_service

                log.warning(
                    "auth.entitlements_fail_closed",
                    tenant_id=str(tenant.id),
                )
                entitlements = entitlement_service.fail_closed_entitlements(
                    entitlement_service.resolve_plan_code_for_tenant(tenant)
                )

            await session.commit()
            request.state.user = user
            request.state.tenant = tenant
            request.state.membership = membership
            request.state.entitlements = entitlements

            # Flag de conveniencia para templates. Mismo criterio que la
            # dependencia current_superadmin: org SADM + rol admin activo.
            request.state.is_superadmin = is_platform_superadmin(
                tenant=tenant,
                membership=membership,
                user=user,
                admin_clerk_org_id=settings.admin_clerk_org_id,
                allowed_clerk_user_ids=settings.superadmin_clerk_user_id_set,
            )
            request.state.force_password_reset = bool(user.force_password_reset)
        except Exception:
            await session.rollback()
            raise


class AuthMiddleware(BaseHTTPMiddleware):
    """Resuelve user/tenant desde Clerk JWT en cada request."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.user = None
        request.state.tenant = None
        request.state.membership = None
        request.state.auth_missing_organization = False
        request.state.auth_token_invalid = False
        request.state.auth_membership_revoked = False
        request.state.is_superadmin = False
        request.state.force_password_reset = False
        request.state.entitlements = None

        try:
            if _skip_session_resolution(request.url.path):
                return await call_next(request)

            await try_resolve_clerk_session(request)

            # Cookie/Bearer presente pero JWT inválido/expirado: no dejar la UI
            # "aparentemente logada" ni devolver CSRF 403 en POST HTMX.
            if getattr(request.state, "auth_token_invalid", False):
                log.info(
                    "auth.session_expired_redirect",
                    path=request.url.path,
                    method=request.method,
                )
                return _login_redirect_clearing_session(request)

            # Membership local revocada (p. ej. webhook deleted): denegar acceso
            # aunque el JWT aún lleve org_id/rol antiguos.
            if getattr(request.state, "auth_membership_revoked", False):
                log.info(
                    "auth.membership_revoked_redirect",
                    path=request.url.path,
                    method=request.method,
                )
                return _login_redirect_clearing_session(request)

            if (
                request.state.auth_missing_organization
                and not _path_allowed_without_active_organization(request.url.path)
            ):
                return _htmx_aware_redirect(request, "/onboarding")

            if getattr(
                request.state, "force_password_reset", False
            ) and not _is_force_password_exempt(request.url.path):
                return _htmx_aware_redirect(request, "/auth/change-password")

            if (
                request.state.user is not None
                and not request.state.auth_missing_organization
                and request.url.path in SESSION_OPTIONAL_PATHS
            ):
                membership = getattr(request.state, "membership", None)
                role = getattr(membership, "role", None) if membership is not None else "member"
                return _htmx_aware_redirect(request, home_path_for_role(str(role or "member")))

            # Matriz de acceso por rol de organización (admin vs member/viewer).
            membership = getattr(request.state, "membership", None)
            if (
                membership is not None
                and getattr(membership, "is_active", False)
                and not _path_exempt_from_role_guard(request.url.path)
                and not role_can_access_path(str(membership.role), request.url.path)
            ):
                dest = home_path_for_role(str(membership.role))
                log.info(
                    "auth.role_path_denied",
                    path=request.url.path,
                    role=membership.role,
                    redirect=dest,
                )
                return _htmx_aware_redirect(request, dest)

            if _requires_csrf(request):
                # Sin sesión, el fallo no es CSRF: es autenticación.
                if request.state.user is None:
                    log.info(
                        "auth.unauthenticated_mutable_redirect",
                        path=request.url.path,
                        method=request.method,
                    )
                    return _login_redirect_clearing_session(request)
                if not _valid_csrf_request(request):
                    log.warning(
                        "csrf.invalid",
                        path=request.url.path,
                        method=request.method,
                        authenticated=True,
                    )
                    return _csrf_error_response(request)

            return await call_next(request)

        except Exception as exc:
            log.exception("auth_middleware_error", path=request.url.path, error=str(exc))
            resp = Response(status_code=500)
            resp.headers["HX-Trigger"] = json.dumps({"appError": "Internal server error"})
            return resp
