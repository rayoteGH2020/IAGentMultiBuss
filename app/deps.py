from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Annotated, Any, cast

import redis.asyncio as redis
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_redis
from app.core.db import get_sessionmaker, set_tenant_context
from app.core.errors import AuthError, ForbiddenError
from app.models import Membership, Tenant, User


async def current_user(request: Request) -> User:
    if getattr(request.state, "auth_missing_organization", False):
        raise AuthError(
            "No active organization",
            details={"code": "no_active_organization"},
        )
    user = getattr(request.state, "user", None)
    if user is None:
        raise AuthError("Not authenticated")
    return cast("User", user)


async def current_tenant(request: Request) -> Tenant:
    if getattr(request.state, "auth_missing_organization", False):
        raise AuthError(
            "No active organization",
            details={"code": "no_active_organization"},
        )
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise AuthError("No tenant context")
    return cast("Tenant", tenant)


async def current_membership(request: Request) -> Membership:
    if getattr(request.state, "auth_missing_organization", False):
        raise AuthError(
            "No active organization",
            details={"code": "no_active_organization"},
        )
    membership = getattr(request.state, "membership", None)
    if membership is None:
        raise AuthError("No membership")
    return cast("Membership", membership)


CurrentUser = Annotated[User, Depends(current_user)]
CurrentTenant = Annotated[Tenant, Depends(current_tenant)]
CurrentMembership = Annotated[Membership, Depends(current_membership)]


async def get_db(
    tenant: Tenant = Depends(current_tenant),
) -> AsyncIterator[AsyncSession]:
    """Sesión de BD con contexto RLS aplicado."""
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            await set_tenant_context(session, str(tenant.id))
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db_no_tenant() -> AsyncIterator[AsyncSession]:
    """Sesión de BD sin contexto RLS (health, scripts, webhooks)."""
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def require_role(*roles: str) -> Callable[..., Coroutine[Any, Any, Membership]]:
    async def _dep(membership: Membership = Depends(current_membership)) -> Membership:
        if membership.role not in roles:
            raise ForbiddenError(f"Requires role: {', '.join(roles)}")
        return membership

    return _dep


async def get_redis_dep() -> redis.Redis:
    return get_redis()
