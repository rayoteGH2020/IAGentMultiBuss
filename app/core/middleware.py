from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

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
        "/health",
        "/health/db",
        "/health/redis",
        "/api/webhooks/clerk",
    }
)
PUBLIC_PREFIXES = ("/static/", "/docs", "/redoc", "/openapi.json", "/openapi", "/demo")


def _is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


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

        if _is_public(request.url.path):
            return await call_next(request)

        token = self._extract_token(request)
        if not token:
            return await call_next(request)

        try:
            claims = verify_clerk_jwt(token)
        except AuthError:
            return await call_next(request)

        clerk_user_id = claims.get("sub")
        clerk_org_id = org_id_from_claims(claims)
        if not isinstance(clerk_user_id, str) or not clerk_user_id:
            return await call_next(request)
        if not clerk_org_id:
            return await call_next(request)

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

        return await call_next(request)

    @staticmethod
    def _extract_token(request: Request) -> str | None:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth.removeprefix("Bearer ").strip()
        cookie = request.cookies.get("__session")
        if isinstance(cookie, str) and cookie:
            return cookie
        return None
