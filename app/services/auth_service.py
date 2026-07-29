from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import set_tenant_context
from app.core.errors import AuthError
from app.core.security import fetch_clerk_org, fetch_clerk_user
from app.models import Membership, Tenant, User

_VALID_ORG_ROLES = frozenset({"admin", "member", "viewer"})


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


async def resolve_user(db: AsyncSession, clerk_user_id: str) -> User:
    """Obtiene el User local. Si no existe, lo crea pidiendo datos a Clerk."""
    result = await db.execute(select(User).where(User.clerk_user_id == clerk_user_id))
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    clerk_data = await fetch_clerk_user(clerk_user_id)
    primary_id = clerk_data.get("primary_email_address_id")
    email: str | None = None
    for e in clerk_data.get("email_addresses", []):
        if isinstance(e, dict) and e.get("id") == primary_id:
            raw = e.get("email_address")
            if isinstance(raw, str):
                email = raw
                break
    if email is None:
        for e in clerk_data.get("email_addresses", []):
            if isinstance(e, dict):
                raw = e.get("email_address")
                if isinstance(raw, str):
                    email = raw
                    break
    if email is None:
        raise AuthError("User has no primary email in Clerk")

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
        plan="free",
    )
    db.add(tenant)
    await db.flush()
    return tenant


async def ensure_membership(
    db: AsyncSession,
    user_id: UUID,
    tenant_id: UUID,
    role: str = "member",
    *,
    allow_reactivation: bool = False,
) -> Membership:
    """Crea o sincroniza una membresía con el rol autoritativo de Clerk."""
    normalized_role = normalize_org_role(role)
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
) -> Membership:
    """Sincroniza y reactiva una membresía desde un evento firmado de Clerk."""
    user = await resolve_user(db, clerk_user_id)
    tenant = await resolve_tenant(db, clerk_org_id)
    await set_tenant_context(db, str(tenant.id))
    return await ensure_membership(
        db,
        user.id,
        tenant.id,
        role=role,
        allow_reactivation=True,
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
