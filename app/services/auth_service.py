from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.db import set_tenant_context
from app.core.errors import AuthError
from app.core.permissions import ORG_ADMIN_ROLE, ORG_CO_ADMIN_ROLE
from app.core.security import clerk_user_exists, fetch_clerk_org, fetch_clerk_user
from app.models import Membership, Tenant, User

log = structlog.get_logger(__name__)

_VALID_ORG_ROLES = frozenset({ORG_ADMIN_ROLE, ORG_CO_ADMIN_ROLE, "member", "viewer"})
# Dominio reservado (RFC 2606): los emails anonimizados nunca son entregables.
DELETED_USER_EMAIL_DOMAIN = "deleted.invalid"


async def get_user_by_id(db: AsyncSession, user_id: UUID) -> User:
    """Carga un User por id local. Levanta AuthError si no existe."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise AuthError("User not found")
    return user


async def clear_force_password_reset(db: AsyncSession, user_id: UUID) -> None:
    """Marca force_password_reset=False tras cambio de contraseña en Clerk."""
    user = await get_user_by_id(db, user_id)
    user.force_password_reset = False
    await db.flush()


def _clerk_email(clerk_data: dict[str, Any]) -> tuple[str, bool]:
    """(email principal, verificado) del perfil de Clerk; el primero si no hay principal."""
    addresses = [e for e in clerk_data.get("email_addresses", []) if isinstance(e, dict)]
    primary_id = clerk_data.get("primary_email_address_id")
    ordered = sorted(addresses, key=lambda e: e.get("id") != primary_id)
    for entry in ordered:
        raw = entry.get("email_address")
        if isinstance(raw, str) and raw:
            verification = entry.get("verification")
            status = verification.get("status") if isinstance(verification, dict) else None
            return raw, status == "verified"
    raise AuthError("User has no primary email in Clerk")


async def detach_deleted_clerk_user(db: AsyncSession, user: User) -> None:
    """Desvincula y anonimiza un usuario local cuyo usuario de Clerk se borró.

    Libera el email (un alta nueva en Clerk con ese email crea un usuario
    limpio, sin heredar permisos ni historial) y desactiva sus membresías en
    todos los tenants. La fila se conserva por las FK (audit_log, documentos).
    ``memberships`` tiene RLS por tenant: se recorren los tenants fijando el
    contexto de cada uno. Es un evento raro (borrado de cuenta).
    """
    user_id = user.id
    tenant_ids = (await db.execute(select(Tenant.id))).scalars().all()
    for tenant_id in tenant_ids:
        await set_tenant_context(db, str(tenant_id))
        await db.execute(
            update(Membership)
            .where(Membership.user_id == user_id, Membership.is_active.is_(True))
            .values(is_active=False)
        )
    await db.execute(text("SELECT set_config('app.current_tenant', '', true)"))
    user.email = f"deleted+{user_id}@{DELETED_USER_EMAIL_DOMAIN}"
    user.name = None
    user.clerk_user_id = None
    await db.flush()
    log.warning("auth.clerk_user_detached", user_id=str(user_id))


async def handle_clerk_user_deleted(db: AsyncSession, clerk_user_id: str) -> bool:
    """Webhook ``user.deleted``: anonimiza el usuario local si existe."""
    result = await db.execute(select(User).where(User.clerk_user_id == clerk_user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return False
    await detach_deleted_clerk_user(db, user)
    return True


async def _claim_existing_email(
    db: AsyncSession, *, existing: User, clerk_user_id: str, verified: bool
) -> User | None:
    """Resuelve un usuario local con el mismo email y otro (o ningún) id de Clerk.

    Returns:
        El usuario a usar si se vincula; None si se ha anonimizado y hay que
        crear uno nuevo.
    """
    if existing.clerk_user_id is None:
        # Alta desde la app (invitación): se vincula solo con email verificado.
        if not verified:
            raise AuthError("Email not verified in Clerk")
        existing.clerk_user_id = clerk_user_id
        await db.flush()
        log.info("auth.clerk_user_linked", user_id=str(existing.id))
        return existing
    if await clerk_user_exists(existing.clerk_user_id):
        log.error("auth.clerk_email_conflict", user_id=str(existing.id))
        raise AuthError("Email already linked to another Clerk user")
    await detach_deleted_clerk_user(db, existing)
    return None


async def resolve_user(db: AsyncSession, clerk_user_id: str) -> User:
    """Obtiene el User local. Si no existe, lo crea pidiendo datos a Clerk.

    Si ya hay un usuario local con ese email: se vincula si se dio de alta
    desde la app sin id de Clerk (invitación), o se anonimiza el antiguo si su
    usuario de Clerk fue borrado (ver ``detach_deleted_clerk_user``).
    """
    result = await db.execute(select(User).where(User.clerk_user_id == clerk_user_id))
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    clerk_data = await fetch_clerk_user(clerk_user_id)
    email, verified = _clerk_email(clerk_data)

    existing = (
        await db.execute(select(User).where(func.lower(User.email) == email.lower()))
    ).scalar_one_or_none()
    if existing is not None:
        linked = await _claim_existing_email(
            db, existing=existing, clerk_user_id=clerk_user_id, verified=verified
        )
        if linked is not None:
            return linked

    name_parts = [clerk_data.get("first_name"), clerk_data.get("last_name")]
    name = " ".join(p for p in name_parts if p) or email.split("@", maxsplit=1)[0]

    user = User(clerk_user_id=clerk_user_id, email=email, name=name)
    db.add(user)
    await db.flush()
    return user


async def resolve_tenant(db: AsyncSession, clerk_org_id: str) -> Tenant:
    """Obtiene el Tenant local. Si no existe, lo crea desde Clerk."""
    result = await db.execute(select(Tenant).where(Tenant.clerk_org_id == clerk_org_id))
    tenant = result.scalar_one_or_none()
    if tenant is not None:
        return tenant

    clerk_data = await fetch_clerk_org(clerk_org_id)
    raw_name = clerk_data.get("name")
    display_name = raw_name if isinstance(raw_name, str) and raw_name else "Sin nombre"
    tenant = Tenant(
        clerk_org_id=clerk_org_id,
        name=display_name,
        plan="basic",
        plan_code="basic",
    )
    db.add(tenant)
    await db.flush()
    return tenant


async def _enforce_single_admin(
    db: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
    role: str,
) -> str:
    """Un solo admin (dueño) por tenant: un segundo admin de Clerk queda como co_admin.

    Degradar es fail-safe (mínimo privilegio) y se corrige solo: cuando el SADM
    quita el rol admin al dueño anterior, el siguiente JWT del nuevo lo sincroniza.
    La org SADM queda exenta: sus admins son los superadmins de plataforma.
    """
    if role != ORG_ADMIN_ROLE:
        return role
    tenant = await db.get(Tenant, tenant_id)
    admin_org = get_settings().admin_clerk_org_id.strip()
    if tenant is not None and admin_org and tenant.clerk_org_id == admin_org:
        return role
    other_admin = await db.scalar(
        select(Membership.id)
        .where(
            Membership.tenant_id == tenant_id,
            Membership.user_id != user_id,
            Membership.role == ORG_ADMIN_ROLE,
            Membership.is_active.is_(True),
        )
        .limit(1)
    )
    if other_admin is None:
        return role
    log.error(
        "membership.duplicate_admin_downgraded",
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        hint="Ya hay un admin activo; asigna org:co_admin en Clerk o retira el admin anterior.",
    )
    return ORG_CO_ADMIN_ROLE


async def ensure_membership(
    db: AsyncSession,
    user_id: UUID,
    tenant_id: UUID,
    role: str = "member",
    *,
    allow_reactivation: bool = False,
) -> Membership:
    """Crea o sincroniza una membresía con el rol autoritativo de Clerk.

    Política:
    - Membership activa: el rol del JWT/evento actualiza la fila local.
    - Membership inactiva (revocada por webhook deleted): un JWT obsoleto
      no reactiva ni cambia el rol. Solo ``allow_reactivation=True``
      (eventos firmados created/updated) puede reactivar.
    """
    normalized_role = await _enforce_single_admin(db, tenant_id, user_id, normalize_org_role(role))
    result = await db.execute(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.tenant_id == tenant_id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership is not None:
        if membership.is_active or allow_reactivation:
            membership.role = normalized_role
        if allow_reactivation:
            if not membership.is_active:
                # Vuelve tras una baja: la solicitud anterior ya no aplica.
                membership.clear_removal_request()
            membership.is_active = True
        await db.flush()
        return membership

    membership = Membership(user_id=user_id, tenant_id=tenant_id, role=normalized_role)
    db.add(membership)
    await db.flush()
    return membership


async def sync_clerk_membership(
    db: AsyncSession,
    clerk_user_id: str,
    clerk_org_id: str,
    role: str,
    *,
    allow_reactivation: bool = True,
) -> Membership:
    """Sincroniza una membresía desde un evento firmado de Clerk.

    ``allow_reactivation`` debe ser True solo para ``organizationMembership.created``
    (alta real en Clerk); en ``updated`` una membership inactiva sigue inactiva.
    """
    user = await resolve_user(db, clerk_user_id)
    tenant = await resolve_tenant(db, clerk_org_id)
    await set_tenant_context(db, str(tenant.id))
    return await ensure_membership(
        db,
        user.id,
        tenant.id,
        role=role,
        allow_reactivation=allow_reactivation,
    )


async def revoke_clerk_membership(
    db: AsyncSession,
    clerk_user_id: str,
    clerk_org_id: str,
) -> bool:
    """Revoca una membresía local sin permitir que un JWT obsoleto la recree."""
    user_result = await db.execute(select(User).where(User.clerk_user_id == clerk_user_id))
    tenant_result = await db.execute(select(Tenant).where(Tenant.clerk_org_id == clerk_org_id))
    user = user_result.scalar_one_or_none()
    tenant = tenant_result.scalar_one_or_none()
    if user is None or tenant is None:
        return False

    await set_tenant_context(db, str(tenant.id))
    result = await db.execute(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.tenant_id == tenant.id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        return False
    membership.is_active = False
    await db.flush()
    return True


def normalize_org_role(raw_role: str) -> str:
    """Normaliza roles Clerk y degrada roles desconocidos a member."""
    role = raw_role.removeprefix("org:")
    return role if role in _VALID_ORG_ROLES else "member"


def org_id_from_claims(claims: dict[str, Any]) -> str | None:
    """Extrae el organization id de los claims del JWT de Clerk."""
    oid = claims.get("org_id")
    if isinstance(oid, str) and oid:
        return oid
    o = claims.get("o")
    if isinstance(o, dict):
        nested = o.get("id")
        if isinstance(nested, str) and nested:
            return nested
    return None


def org_role_from_claims(claims: dict[str, Any]) -> str:
    """Rol en org: JWT v1 usa org_role; v2 usa o.rol."""
    raw = claims.get("org_role")
    if isinstance(raw, str) and raw:
        return normalize_org_role(raw)
    o = claims.get("o")
    if isinstance(o, dict):
        rol = o.get("rol")
        if isinstance(rol, str) and rol:
            return normalize_org_role(rol)
    return "member"
