"""Tests de membership_service con Clerk mockeado (Paso 30 §E.4)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.core.errors import NotFoundError, ValidationError
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.membership import (
    AppointmentPermissions,
    MembershipPermissions,
    TenantMemberCreate,
    TenantMemberUpdate,
)
from app.services import membership_service
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _tenant_with_org(db_session: AsyncSession) -> Tenant:
    tenant = Tenant(clerk_org_id=f"org_{uuid4().hex[:8]}", name="T", plan="free", settings={})
    db_session.add(tenant)
    await db_session.flush()
    return tenant


@pytest.mark.asyncio
async def test_create_tenant_member_persists_permissions(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)

    async def fake_invitation(org_id: str, email: str, role: str = "org:member") -> dict[str, str]:
        return {"id": "inv_1"}

    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.create_org_invitation",
        fake_invitation,
    )

    async def fake_find_user(email: str) -> None:
        return None

    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.find_user_by_email",
        fake_find_user,
    )

    await set_tenant_context(db_session, str(tenant.id))
    payload = TenantMemberCreate(
        email="new.member@example.com",
        name="New Member",
        role="viewer",
    )
    result = await membership_service.create_tenant_member(db_session, tenant.id, payload)

    assert result.role == "viewer"
    assert result.permissions.appointments.view is True
    assert result.permissions.appointments.create is False

    ms = await db_session.execute(select(Membership).where(Membership.id == result.membership_id))
    membership = ms.scalar_one()
    assert membership.permissions["appointments"]["view"] is True


@pytest.mark.asyncio
async def test_create_tenant_member_uses_add_org_member_for_existing_clerk_user(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)
    clerk_id = f"user_{uuid4().hex[:8]}"
    user = User(clerk_user_id=clerk_id, email="linked@example.com", name="Linked")
    db_session.add(user)
    await db_session.flush()

    calls: list[tuple[str, str, str]] = []

    async def fake_add(org_id: str, clerk_user_id: str, role: str = "org:member") -> dict[str, str]:
        calls.append((org_id, clerk_user_id, role))
        return {"id": clerk_user_id}

    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.add_org_member",
        fake_add,
    )

    async def fake_update_user(*args: object, **kwargs: object) -> dict[str, str]:
        return {}

    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.update_user",
        fake_update_user,
    )

    await set_tenant_context(db_session, str(tenant.id))
    result = await membership_service.create_tenant_member(
        db_session,
        tenant.id,
        TenantMemberCreate(email="linked@example.com", name="Linked User", role="member"),
    )

    assert result.clerk_user_id == clerk_id
    assert calls == [(tenant.clerk_org_id, clerk_id, "org:member")]


@pytest.mark.asyncio
async def test_create_tenant_member_does_not_persist_membership_when_clerk_fails(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)

    async def fake_find_user(email: str) -> None:
        return None

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Clerk invitation failed")

    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.find_user_by_email",
        fake_find_user,
    )
    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.create_org_invitation",
        boom,
    )

    await set_tenant_context(db_session, str(tenant.id))
    with pytest.raises(RuntimeError, match="Clerk invitation failed"):
        await membership_service.create_tenant_member(
            db_session,
            tenant.id,
            TenantMemberCreate(email="fail@example.com", name="Fail", role="member"),
        )

    count = await db_session.scalar(
        select(func.count()).select_from(Membership).where(Membership.tenant_id == tenant.id)
    )
    assert count == 0


@pytest.mark.asyncio
async def test_create_tenant_member_rejects_duplicate(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)
    user = User(email="dup@example.com", name="Dup")
    db_session.add(user)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    db_session.add(Membership(user_id=user.id, tenant_id=tenant.id, role="member"))
    await db_session.flush()

    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.create_org_invitation",
        lambda *a, **k: {"id": "inv"},
    )

    with pytest.raises(ValidationError, match="already a member"):
        await membership_service.create_tenant_member(
            db_session,
            tenant.id,
            TenantMemberCreate(email="dup@example.com", name="Dup", role="viewer"),
        )


@pytest.mark.asyncio
async def test_create_tenant_member_reactivates_revoked_membership(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)
    clerk_id = f"user_{uuid4().hex[:8]}"
    user = User(clerk_user_id=clerk_id, email="return@example.com", name="Return")
    db_session.add(user)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    membership = Membership(
        user_id=user.id,
        tenant_id=tenant.id,
        role="member",
        is_active=False,
    )
    db_session.add(membership)
    await db_session.flush()

    async def fake_add(*args: object, **kwargs: object) -> dict[str, str]:
        return {"id": clerk_id}

    async def fake_update(*args: object, **kwargs: object) -> dict[str, str]:
        return {}

    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.add_org_member",
        fake_add,
    )
    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.update_user",
        fake_update,
    )

    result = await membership_service.create_tenant_member(
        db_session,
        tenant.id,
        TenantMemberCreate(email=user.email, name="Return", role="viewer"),
    )

    assert result.membership_id == membership.id
    assert membership.is_active is True
    assert membership.role == "viewer"


@pytest.mark.asyncio
async def test_update_tenant_member_permissions_local_only_no_clerk(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)
    user = User(clerk_user_id=f"user_{uuid4().hex[:8]}", email="perm@example.com", name="U")
    db_session.add_all([tenant, user])
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    membership = Membership(user_id=user.id, tenant_id=tenant.id, role="member")
    db_session.add(membership)
    await db_session.flush()
    original_role = membership.role

    async def boom_clerk(*args: object, **kwargs: object) -> None:
        raise AssertionError("Clerk must not be called on member update")

    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.update_org_member_role",
        boom_clerk,
    )
    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.update_user",
        boom_clerk,
    )

    await set_tenant_context(db_session, str(tenant.id))
    perms = MembershipPermissions(
        appointments=AppointmentPermissions(view=True, create=True, edit=False, cancel=False),
    )
    updated = await membership_service.update_tenant_member(
        db_session,
        tenant.id,
        membership.id,
        TenantMemberUpdate(permissions=perms),
    )
    assert updated.permissions.appointments.create is True
    assert updated.role == original_role


@pytest.mark.asyncio
async def test_remove_tenant_member_revokes_local_row_without_calling_clerk(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)
    clerk_id = f"user_{uuid4().hex[:8]}"
    user = User(clerk_user_id=clerk_id, email="remove@example.com", name="Remove")
    db_session.add_all([tenant, user])
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    membership = Membership(user_id=user.id, tenant_id=tenant.id, role="member")
    db_session.add(membership)
    await db_session.flush()
    membership_id = membership.id

    async def boom_remove(*args: object, **kwargs: object) -> None:
        raise AssertionError("Clerk remove_org_member must not be called")

    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.remove_org_member",
        boom_remove,
    )

    await set_tenant_context(db_session, str(tenant.id))
    await membership_service.remove_tenant_member(db_session, tenant.id, membership_id)

    stored = await db_session.get(Membership, membership_id)
    assert stored is not None
    assert stored.is_active is False


@pytest.mark.asyncio
async def test_list_tenant_members_returns_joined_rows(
    db_session: AsyncSession,
) -> None:
    tenant = await _tenant_with_org(db_session)
    user = User(email="listed@example.com", name="Listed")
    db_session.add_all([tenant, user])
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    db_session.add(Membership(user_id=user.id, tenant_id=tenant.id, role="viewer"))
    await db_session.flush()

    await set_tenant_context(db_session, str(tenant.id))
    members = await membership_service.list_tenant_members(db_session, tenant.id)

    assert len(members) == 1
    assert members[0].email == "listed@example.com"
    assert members[0].role == "viewer"


@pytest.mark.asyncio
async def test_remove_tenant_member_not_found(
    db_session: AsyncSession,
) -> None:
    tenant = await _tenant_with_org(db_session)
    await set_tenant_context(db_session, str(tenant.id))
    with pytest.raises(NotFoundError, match="Membership not found"):
        await membership_service.remove_tenant_member(db_session, tenant.id, uuid4())


def test_app_role_to_clerk_role_mapping() -> None:
    assert membership_service.app_role_to_clerk_role("admin") == "org:admin"
    assert membership_service.app_role_to_clerk_role("viewer") == "org:member"
    assert membership_service.app_role_to_clerk_role("unknown") == "org:member"
