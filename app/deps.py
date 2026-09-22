from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Annotated, Any, cast

import redis.asyncio as redis
import structlog
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_redis
from app.core.db import get_sessionmaker, set_tenant_context
from app.core.errors import AuthError, ForbiddenError, PlanRequiredError, ValidationError
from app.core.permissions import is_platform_superadmin
from app.models import Membership, Tenant, User
from app.schemas.entitlements import Entitlements
from app.services import entitlement_service

log = structlog.get_logger(__name__)


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


async def current_superadmin(request: Request) -> Tenant:
    """Restringe el acceso al superadmin de plataforma.

    No basta con pertenecer a `ADMIN_CLERK_ORG_ID`: se exige rol `admin`
    activo en esa organización (y allowlist opcional). Se revalida aquí en
    lugar de confiar en `request.state.is_superadmin`.
    """
    from app.config import get_settings

    tenant = await current_tenant(request)
    user = await current_user(request)
    membership = await current_membership(request)
    settings = get_settings()
    if not is_platform_superadmin(
        tenant=tenant,
        membership=membership,
        user=user,
        admin_clerk_org_id=settings.admin_clerk_org_id,
        allowed_clerk_user_ids=settings.superadmin_clerk_user_id_set,
    ):
        log.warning(
            "sadm.access_denied",
            path=request.url.path,
            clerk_user_id=user.clerk_user_id,
            clerk_org_id=tenant.clerk_org_id,
            role=membership.role,
        )
        raise ForbiddenError("Superadmin access required")
    return tenant


SuperAdmin = Annotated[Tenant, Depends(current_superadmin)]


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


# Alias listo para usar en cualquier ruta que requiera rol admin.
# Mismo patrón que CurrentUser/CurrentTenant: importar desde deps en lugar de
# redeclarar RequireAdmin en cada módulo de rutas.
RequireAdmin = Annotated[Membership, Depends(require_role("admin"))]


def require_appointment_permission(
    *actions: str,
) -> Callable[..., Coroutine[Any, Any, Membership]]:
    """Factory: al menos uno de los permisos de citas (admin bypass)."""

    from app.core.permissions import membership_can_appointment

    async def _dep(membership: Membership = Depends(current_membership)) -> Membership:
        if membership.role == "admin":
            return membership
        for action in actions:
            if membership_can_appointment(membership, action):  # type: ignore[arg-type]
                return membership
        raise ForbiddenError(f"Requires appointment permission: {', '.join(actions)}")

    return _dep


RequireAppointmentView = Annotated[Membership, Depends(require_appointment_permission("view"))]
RequireAppointmentCreate = Annotated[Membership, Depends(require_appointment_permission("create"))]
RequireAppointmentEdit = Annotated[Membership, Depends(require_appointment_permission("edit"))]
RequireAppointmentCancel = Annotated[Membership, Depends(require_appointment_permission("cancel"))]
RequireAppointmentCreateOrEdit = Annotated[
    Membership, Depends(require_appointment_permission("create", "edit"))
]


async def get_entitlements(
    request: Request,
    tenant: Tenant = Depends(current_tenant),
    db: AsyncSession = Depends(get_db),
) -> Entitlements:
    """Resuelve entitlements una vez por request (cache en ``request.state``)."""
    cached = getattr(request.state, "entitlements", None)
    if isinstance(cached, Entitlements):
        return cached
    try:
        ents = await entitlement_service.resolve_entitlements(db, tenant)
    except ValidationError:
        log.warning(
            "entitlements.resolve_failed",
            tenant_id=str(tenant.id),
            path=getattr(request.url, "path", None),
        )
        ents = entitlement_service.fail_closed_entitlements(
            entitlement_service.resolve_plan_code_for_tenant(tenant)
        )
    request.state.entitlements = ents
    return ents


EntitlementsDep = Annotated[Entitlements, Depends(get_entitlements)]


def require_feature(feature: str) -> Callable[..., Coroutine[Any, Any, Entitlements]]:
    """Factory: deniega con ``PlanRequiredError`` si el plan no incluye ``feature``."""

    async def _dep(ents: Entitlements = Depends(get_entitlements)) -> Entitlements:
        if not ents.has(feature):
            raise PlanRequiredError(feature)
        return ents

    return _dep


def require_any_feature(
    *features: str,
) -> Callable[..., Coroutine[Any, Any, Entitlements]]:
    """Factory: basta con una de las features (p. ej. chat documental o knowledge)."""
    if not features:
        msg = "require_any_feature requires at least one feature code"
        raise ValueError(msg)

    async def _dep(ents: Entitlements = Depends(get_entitlements)) -> Entitlements:
        if any(ents.has(code) for code in features):
            return ents
        raise PlanRequiredError(features[0])

    return _dep


RequireDocuments = Annotated[Entitlements, Depends(require_feature("documents"))]
RequireKnowledge = Annotated[Entitlements, Depends(require_feature("knowledge"))]
RequireCalendarGoogle = Annotated[Entitlements, Depends(require_feature("calendar_google"))]
RequireCalendarVoice = Annotated[Entitlements, Depends(require_feature("calendar_voice"))]
RequireAppointments = Annotated[Entitlements, Depends(require_feature("appointments"))]
RequireChat = Annotated[
    Entitlements,
    Depends(require_any_feature("documents_chat", "knowledge_chat")),
]


async def get_redis_dep() -> redis.Redis:
    # Wrapper fino para usar get_redis() como dependencia FastAPI.
    # Necesario porque FastAPI espera una función/coroutine invocable como
    # dependencia; no puede usar get_redis directamente (es una función sync
    # que devuelve un cliente, no un generador async).
    return get_redis()


RedisDep = Annotated[redis.Redis, Depends(get_redis_dep)]
