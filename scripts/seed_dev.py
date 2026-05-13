"""Seed de datos para desarrollo local.

Uso: infisical run -- uv run python scripts/seed_dev.py
"""

import asyncio

import structlog
from app.core.db import session_scope, set_tenant_context
from app.models import Membership, Tenant, User
from sqlalchemy import select

log = structlog.get_logger(__name__)


async def main() -> None:
    async with session_scope() as s:
        tr = await s.execute(select(Tenant).where(Tenant.name == "Panadería Pepe"))
        tenant = tr.scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(name="Panadería Pepe", plan="starter")
            s.add(tenant)
            await s.flush()

        ur = await s.execute(select(User).where(User.email == "pepe@panaderia.com"))
        user = ur.scalar_one_or_none()
        if user is None:
            user = User(email="pepe@panaderia.com", name="Pepe")
            s.add(user)
            await s.flush()

        mr = await s.execute(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.tenant_id == tenant.id,
            )
        )
        if mr.scalar_one_or_none() is None:
            await set_tenant_context(s, str(tenant.id))
            s.add(Membership(user_id=user.id, tenant_id=tenant.id, role="admin"))

    log.info("seed_completed", tenant_id=str(tenant.id))


if __name__ == "__main__":
    asyncio.run(main())
