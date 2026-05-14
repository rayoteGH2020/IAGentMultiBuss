from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from app.core.db import get_sessionmaker, set_tenant_context
from app.core.errors import AuthError
from app.core.security import verify_clerk_jwt
from app.services.auth_service import (
    ensure_membership,
    org_id_from_claims,
    org_role_from_claims,
    resolve_tenant,
    resolve_user,
)

PUBLIC_PATHS = frozenset(
    {
        "/login",
        "/signup",
        "/auth/organization",
        "/health",
        "/health/db",
        "/health/redis",
        "/api/webhooks/clerk",
    }
)
PUBLIC_PREFIXES = ("/static/", "/docs", "/redoc", "/openapi.json", "/openapi", "/demo")

SESSION_OPTIONAL_PATHS = frozenset(
    {"/login", "/signup", "/auth/organization", "/onboarding"},
)


def _is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def _skip_session_resolution(path: str) -> bool:
    """Rutas donde no hace falta leer JWT ni tocar BD (salvo login/signup/org/onboarding)."""
    if path in SESSION_OPTIONAL_PATHS:
        return False
    return _is_public(path)


def _path_allowed_without_active_organization(path: str) -> bool:
    """Rutas accesibles con sesión Clerk pero sin organización activa en el JWT."""
    if path in SESSION_OPTIONAL_PATHS:
        return True
    if path in frozenset({"/logout"}):
        return True
    return _is_public(path)


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
    token = _extract_token(request)
    if not token:
        return
    try:
        claims = verify_clerk_jwt(token)
    except AuthError:
        return

    clerk_user_id = claims.get("sub")
    clerk_org_id = org_id_from_claims(claims)
    if not isinstance(clerk_user_id, str) or not clerk_user_id:
        return
    if not clerk_org_id:
        request.state.auth_missing_organization = True
        return

    sm = get_sessionmaker()
    async with sm() as session:
        try:
            user = await resolve_user(session, clerk_user_id)
            tenant = await resolve_tenant(session, clerk_org_id)
            await set_tenant_context(session, str(tenant.id))
            membership = await ensure_membership(
                session,
                user.id,
                tenant.id,
                role=org_role_from_claims(claims),
            )
            await session.commit()
            request.state.user = user
            request.state.tenant = tenant
            request.state.membership = membership
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

        if _skip_session_resolution(request.url.path):
            return await call_next(request)

        await try_resolve_clerk_session(request)

        if (
            request.state.auth_missing_organization
            and not _path_allowed_without_active_organization(request.url.path)
        ):
            return RedirectResponse(url="/onboarding", status_code=302)

        if request.state.user is not None and request.url.path in SESSION_OPTIONAL_PATHS:
            return RedirectResponse(url="/", status_code=302)

        return await call_next(request)
