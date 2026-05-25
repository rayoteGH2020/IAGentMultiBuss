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
    # auth_missing_organization: el middleware setea este flag cuando el JWT
    # es válido (usuario autenticado) pero no tiene organización activa en
    # Clerk. Es un estado de onboarding distinto a "no autenticado"; se trata
    # primero para dar un mensaje y comportamiento específico.
    if getattr(request.state, "auth_missing_organization", False):
        raise AuthError(
            "No active organization",
            details={"code": "no_active_organization"},
        )
    # getattr con None: request.state es dinámico; sin el default, acceder
    # a un atributo no seteado (request sin auth middleware) lanzaría AttributeError.
    user = getattr(request.state, "user", None)
    if user is None:
        raise AuthError("Not authenticated")
    # cast informa a mypy del tipo concreto; getattr devuelve Any.
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


# Aliases Annotated: permiten usar `CurrentUser` como tipo en la firma del
# handler en lugar de `User = Depends(current_user)`, lo que es más conciso
# y el patrón recomendado por FastAPI desde la v2. El prefijo _ en la firma
# del handler (_user: CurrentUser) indica que la dependencia es requerida
# para sus efectos secundarios (validación de auth) pero no se usa en el cuerpo.
CurrentUser = Annotated[User, Depends(current_user)]
CurrentTenant = Annotated[Tenant, Depends(current_tenant)]
CurrentMembership = Annotated[Membership, Depends(current_membership)]


async def get_db(
    # Depende de current_tenant: si el usuario no está autenticado, FastAPI
    # resuelve esta dependencia antes de get_db y lanza AuthError, evitando
    # abrir una conexión a BD para una petición que se rechazará de todos modos.
    tenant: Tenant = Depends(current_tenant),
) -> AsyncIterator[AsyncSession]:
    """Sesión de BD con contexto RLS aplicado al tenant del request."""
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            # set_tenant_context ANTES del yield: RLS está activo desde la
            # primera query del handler. Sin esto, las queries anteriores al
            # primer flush verían datos de todos los tenants.
            await set_tenant_context(session, str(tenant.id))
            yield session
            # commit automático al salir del handler sin excepción: los handlers
            # no necesitan llamar a db.commit() explícitamente (salvo cuando
            # necesitan confirmar antes del final, como en upload_invoices).
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db_no_tenant() -> AsyncIterator[AsyncSession]:
    """Sesión de BD sin contexto de tenant ni RLS.

    Para endpoints que necesitan ver datos de todos los tenants (métricas
    cross-tenant, health checks, webhooks que crean tenants nuevos). No
    depende de current_tenant: se puede llamar sin token de auth.
    """
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def require_role(*roles: str) -> Callable[..., Coroutine[Any, Any, Membership]]:
    """Factory de dependencia para control de acceso por rol.

    Uso: `Depends(require_role("admin"))` o `Depends(require_role("admin", "member"))`.
    Se acepta *roles para poder requerir varios roles con una sola dependencia,
    en lugar de una dependencia diferente por cada combinación posible.
    """

    async def _dep(membership: Membership = Depends(current_membership)) -> Membership:
        if membership.role not in roles:
            raise ForbiddenError(f"Requires role: {', '.join(roles)}")
        return membership

    return _dep


async def get_redis_dep() -> redis.Redis:
    # Wrapper fino para usar get_redis() como dependencia FastAPI.
    # Necesario porque FastAPI espera una función/coroutine invocable como
    # dependencia; no puede usar get_redis directamente (es una función sync
    # que devuelve un cliente, no un generador async).
    return get_redis()


RedisDep = Annotated[redis.Redis, Depends(get_redis_dep)]
