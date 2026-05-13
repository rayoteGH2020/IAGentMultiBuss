import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.models import Membership, Tenant, User
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

# Local test DB user/password (not production); detect-secrets allowlist for URL shape.
_DEFAULT_RLS_URL = (
    "postgresql+asyncpg://saas_app:saas@localhost:5432/saas"  # pragma: allowlist secret
)


def _rls_database_url() -> str:
    return os.environ.get("RLS_TEST_DATABASE_URL", _DEFAULT_RLS_URL)


@asynccontextmanager
async def _rls_session_scope() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_rls_database_url(), pool_pre_ping=True)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    await engine.dispose()


@pytest.mark.asyncio
async def test_membership_rls_isolation() -> None:
    """Verifica que dos tenants no se ven mutuamente (rol sin BYPASSRLS)."""
    suffix = str(uuid4())[:8]
    async with _rls_session_scope() as s:
        t1 = Tenant(name=f"Tenant RLS 1 {suffix}")
        t2 = Tenant(name=f"Tenant RLS 2 {suffix}")
        u1 = User(email=f"rls_u1_{suffix}@test.local", name="U1")
        u2 = User(email=f"rls_u2_{suffix}@test.local", name="U2")
        s.add_all([t1, t2, u1, u2])
        await s.flush()

        await set_tenant_context(s, str(t1.id))
        s.add(Membership(user_id=u1.id, tenant_id=t1.id, role="admin"))
        await s.flush()

        await set_tenant_context(s, str(t2.id))
        s.add(Membership(user_id=u2.id, tenant_id=t2.id, role="admin"))
        await s.flush()

        t1_id, t2_id = t1.id, t2.id

    async with _rls_session_scope() as s:
        await set_tenant_context(s, str(t1_id))
        result = await s.execute(select(Membership))
        memberships = result.scalars().all()
        assert len(memberships) == 1
        assert memberships[0].tenant_id == t1_id

    async with _rls_session_scope() as s:
        await set_tenant_context(s, str(t2_id))
        result = await s.execute(select(Membership))
        memberships = result.scalars().all()
        assert len(memberships) == 1
        assert memberships[0].tenant_id == t2_id

    async with _rls_session_scope() as s:
        await s.execute(text("TRUNCATE memberships, users, tenants CASCADE"))
