from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthError
from app.core.security import fetch_clerk_org, fetch_clerk_user
from app.models import Membership, Tenant, User


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
    db: AsyncSession, user_id: UUID, tenant_id: UUID, role: str = "member"
) -> Membership:
    result = await db.execute(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.tenant_id == tenant_id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership is not None:
        return membership

    membership = Membership(user_id=user_id, tenant_id=tenant_id, role=role)
    db.add(membership)
    await db.flush()
    return membership


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
    raw = claims.get("org_role")
    if not isinstance(raw, str) or not raw:
        return "member"
    return raw.replace("org:", "")
