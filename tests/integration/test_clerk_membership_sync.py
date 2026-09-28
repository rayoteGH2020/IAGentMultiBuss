"""Tests de sincronización y revocación de memberships desde Clerk."""

from datetime import UTC, date, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.core.permissions import role_can_access_path
from app.models import Membership, Tenant, User
from app.services.auth_service import (
    ensure_membership,
    revoke_clerk_membership,
    sync_clerk_membership,
)
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
async def test_downgrade_admin_to_member_via_clerk_sync(
    db_session: AsyncSession,
) -> None:
    """organizationMembership.updated (admin → member) pierde rutas de admin."""
    tenant, user, membership = await _membership_fixture(db_session, role="admin")
    assert role_can_access_path("admin", "/documents") is True

    result = await sync_clerk_membership(
        db_session,
        user.clerk_user_id or "",
        tenant.clerk_org_id or "",
        "org:member",
    )

    assert result.id == membership.id
    assert result.role == "member"
    assert result.is_active is True
    assert role_can_access_path(result.role, "/documents") is False
    assert role_can_access_path(result.role, "/chat") is True


async def _second_user_in(db: AsyncSession, tenant: Tenant) -> User:
    suffix = uuid4().hex[:12]
    user = User(clerk_user_id=f"user_{suffix}", email=f"{suffix}@test.local", name="Second")
    db.add(user)
    await db.flush()
    await set_tenant_context(db, str(tenant.id))
    return user


@pytest.mark.asyncio
async def test_second_admin_is_downgraded_to_co_admin(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.auth_service.get_settings",
        lambda: MagicMock(admin_clerk_org_id="org_sadm"),
    )
    tenant, _owner, _ = await _membership_fixture(db_session, role="admin")
    second = await _second_user_in(db_session, tenant)

    result = await ensure_membership(db_session, second.id, tenant.id, role="org:admin")

    assert result.role == "co_admin"


@pytest.mark.asyncio
async def test_sole_admin_keeps_admin_role(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.auth_service.get_settings",
        lambda: MagicMock(admin_clerk_org_id="org_sadm"),
    )
    tenant, user, membership = await _membership_fixture(db_session, role="admin")

    result = await ensure_membership(db_session, user.id, tenant.id, role="org:admin")

    assert result.id == membership.id
    assert result.role == "admin"


@pytest.mark.asyncio
async def test_sadm_org_allows_several_admins(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Los admins de la org SADM son superadmins de plataforma: no se degradan."""
    tenant, _owner, _ = await _membership_fixture(db_session, role="admin")
    monkeypatch.setattr(
        "app.services.auth_service.get_settings",
        lambda: MagicMock(admin_clerk_org_id=tenant.clerk_org_id),
    )
    second = await _second_user_in(db_session, tenant)

    result = await ensure_membership(db_session, second.id, tenant.id, role="org:admin")

    assert result.role == "admin"


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
async def test_reactivation_clears_pending_removal_but_active_sync_keeps_it(
    db_session: AsyncSession,
) -> None:
    tenant, user, membership = await _membership_fixture(db_session, role="member")
    membership.removal_requested_at = datetime.now(UTC)
    membership.removal_effective_date = date(2026, 10, 9)
    await db_session.flush()

    # Sync de rol con la membership activa (JWT o webhook updated): sigue pendiente.
    await ensure_membership(db_session, user.id, tenant.id, role="org:member")
    assert membership.removal_pending is True

    # Baja ejecutada en Clerk y posterior reactivación: la solicitud ya no aplica.
    membership.is_active = False
    await db_session.flush()
    await ensure_membership(
        db_session, user.id, tenant.id, role="org:member", allow_reactivation=True
    )
    assert membership.is_active is True
    assert membership.removal_pending is False


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


@pytest.mark.asyncio
async def test_stale_jwt_cannot_restore_access_after_delete(
    db_session: AsyncSession,
) -> None:
    """Tras deleted, ensure_membership (ruta JWT) no reactiva aunque el JWT diga admin."""
    tenant, user, membership = await _membership_fixture(db_session, role="admin")
    await revoke_clerk_membership(
        db_session,
        user.clerk_user_id or "",
        tenant.clerk_org_id or "",
    )

    result = await ensure_membership(
        db_session,
        user.id,
        tenant.id,
        role="org:admin",
    )

    assert result.is_active is False
    assert result.role == "admin"
    assert membership.is_active is False
