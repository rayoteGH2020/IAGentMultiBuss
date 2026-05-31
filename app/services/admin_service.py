"""Admin service: gestión de orgs y usuarios por el SuperAdmin (Paso 50).

Orquesta llamadas a Clerk + BD. Las rutas llaman aquí, nunca a clerk_client directamente.
"""

from __future__ import annotations

import secrets
import string
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clerk_client
from app.core.db import set_tenant_context
from app.core.errors import NotFoundError
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.user import User
from app.services.auth_service import ensure_membership, resolve_tenant, resolve_user

log = structlog.get_logger(__name__)


def _generate_temp_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def create_org_with_tenant(db: AsyncSession, name: str) -> Tenant:
    """Crea una organización en Clerk y su Tenant equivalente en BD.

    Si el insert en BD falla, intenta eliminar la org en Clerk (best-effort).
    El webhook organization.created actúa como safety net de convergencia.
    """
    clerk_org = await clerk_client.create_organization(name)
    clerk_org_id: str = clerk_org["id"]

    try:
        tenant = await resolve_tenant(db, clerk_org_id)
        await db.flush()
    except Exception:
        try:
            await clerk_client.delete_organization(clerk_org_id)
        except Exception:
            log.error("admin.clerk_rollback_failed", clerk_org_id=clerk_org_id)
        raise

    log.info("admin.org_created", clerk_org_id=clerk_org_id, name=name)
    return tenant


async def create_user_in_org(
    db: AsyncSession,
    email: str,
    first_name: str,
    last_name: str,
    tenant_id: UUID,
    role: str = "member",
) -> User:
    """Crea un usuario en Clerk, lo añade a la org y registra force_password_reset.

    La contraseña temporal nunca se expone: el usuario debe cambiarla en el primer login.
    """
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None or tenant.clerk_org_id is None:
        raise NotFoundError(f"Tenant {tenant_id} not found or not linked to Clerk")

    temp_password = _generate_temp_password()
    clerk_user = await clerk_client.create_user(
        email=email,
        password=temp_password,
        first_name=first_name,
        last_name=last_name,
    )
    clerk_user_id: str = clerk_user["id"]

    try:
        await clerk_client.add_org_member(tenant.clerk_org_id, clerk_user_id, role=f"org:{role}")
        # set_tenant_context obligatorio: memberships tiene FORCE ROW LEVEL SECURITY.
        await set_tenant_context(db, str(tenant_id))
        user = await resolve_user(db, clerk_user_id)
        await ensure_membership(db, user.id, tenant_id, role=role)
        await db.flush()
    except Exception:
        try:
            await clerk_client.delete_user(clerk_user_id)
        except Exception:
            log.error("admin.clerk_user_rollback_failed", clerk_user_id=clerk_user_id)
        raise

    log.info(
        "admin.user_created", clerk_user_id=clerk_user_id, email=email, tenant_id=str(tenant_id)
    )
    return user


async def remove_user_from_org(
    db: AsyncSession,
    user_id: UUID,
    tenant_id: UUID,
) -> None:
    """Elimina la membresía en BD y en Clerk."""
    await set_tenant_context(db, str(tenant_id))

    result = await db.execute(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.tenant_id == tenant_id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise NotFoundError("Membership not found")

    result_user = await db.execute(select(User).where(User.id == user_id))
    user = result_user.scalar_one_or_none()

    result_tenant = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result_tenant.scalar_one_or_none()

    if user and user.clerk_user_id and tenant and tenant.clerk_org_id:
        await clerk_client.remove_org_member(tenant.clerk_org_id, user.clerk_user_id)

    await db.delete(membership)
    await db.flush()
    log.info("admin.user_removed", user_id=str(user_id), tenant_id=str(tenant_id))


async def list_all_tenants(db: AsyncSession) -> list[Tenant]:
    # tenants no tiene tenant_id → sin RLS, no requiere set_tenant_context.
    result = await db.execute(select(Tenant).order_by(Tenant.created_at.desc()))
    return list(result.scalars().all())


async def list_tenant_members(db: AsyncSession, tenant_id: UUID) -> list[tuple[User, Membership]]:
    # memberships tiene FORCE ROW LEVEL SECURITY: contexto obligatorio.
    await set_tenant_context(db, str(tenant_id))
    result = await db.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.tenant_id == tenant_id)
        .order_by(User.email)
    )
    return [(row[0], row[1]) for row in result.all()]
