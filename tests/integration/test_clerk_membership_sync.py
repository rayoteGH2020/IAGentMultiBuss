"""Tests de sincronización y revocación de memberships desde Clerk."""

from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.models import Membership, Tenant, User
from app.services.auth_service import ensure_membership, revoke_clerk_membership
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _membership_fixture(
    db: AsyncSession,
    *,
    role: str = "admin",
) -> tuple[Tenant, User, Membership]:
    suffix = uuid4().hex[:12]
    tenant = Tenant(
        clerk_org_id=f"org_{suffix}",
        name="Clerk Sync",
        plan="free",
        settings={},
    )
    user = User(
        clerk_user_id=f"user_{suffix}",
        email=f"{suffix}@test.local",
        name="Clerk User",
    )
    db.add_all([tenant, user])
    await db.flush()
    await set_tenant_context(db, str(tenant.id))
    membership = Membership(user_id=user.id, tenant_id=tenant.id, role=role)
    db.add(membership)
    await db.flush()
    return tenant, user, membership


@pytest.mark.asyncio
async def test_existing_membership_role_is_synchronized(db_session: AsyncSession) -> None:
    tenant, user, membership = await _membership_fixture(db_session)

    result = await ensure_membership(
        db_session,
        user.id,
        tenant.id,
        role="org:member",
    )

    assert result.id == membership.id
    assert result.role == "member"
    assert result.is_active is True


@pytest.mark.asyncio
async def test_revoked_membership_is_not_reactivated_by_stale_jwt(
    db_session: AsyncSession,
) -> None:
    tenant, user, membership = await _membership_fixture(db_session, role="member")
    membership.is_active = False
    await db_session.flush()

    result = await ensure_membership(
        db_session,
        user.id,
        tenant.id,
        role="org:admin",
    )

    assert result.is_active is False
    assert result.role == "member"


@pytest.mark.asyncio
async def test_authoritative_clerk_event_reactivates_membership(
    db_session: AsyncSession,
) -> None:
    tenant, user, membership = await _membership_fixture(db_session, role="member")
    membership.is_active = False
    await db_session.flush()

    result = await ensure_membership(
        db_session,
        user.id,
        tenant.id,
        role="org:viewer",
        allow_reactivation=True,
    )

    assert result.is_active is True
    assert result.role == "viewer"


@pytest.mark.asyncio
async def test_clerk_delete_event_revokes_local_membership(
    db_session: AsyncSession,
) -> None:
    tenant, user, membership = await _membership_fixture(db_session)

    revoked = await revoke_clerk_membership(
        db_session,
        user.clerk_user_id or "",
        tenant.clerk_org_id or "",
    )

    assert revoked is True
    assert membership.is_active is False
