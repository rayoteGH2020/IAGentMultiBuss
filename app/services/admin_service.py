"""Admin service: lectura cross-tenant para SADM (Paso05).

Provision de orgs/users: solo Clerk Dashboard (Decision_Log D005).
Este servicio no crea identidades ni credenciales.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import set_tenant_context
from app.core.errors import NotFoundError
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.user import User

log = structlog.get_logger(__name__)


async def get_tenant(db: AsyncSession, tenant_id: UUID) -> Tenant:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise NotFoundError(f"Tenant {tenant_id} not found")
    return tenant


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
        .where(
            Membership.tenant_id == tenant_id,
            Membership.is_active.is_(True),
        )
        .order_by(User.email)
    )
    return [(row[0], row[1]) for row in result.all()]
