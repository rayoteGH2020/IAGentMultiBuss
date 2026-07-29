"""Tests de admin_service (Paso 50 / Paso 24 Fase A)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.user import User
from app.services import admin_service
from sqlalchemy import select, text

pytestmark = pytest.mark.integration


@pytest.fixture
async def users_force_reset_column(db_session) -> None:
    result = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'users' "
            "AND column_name = 'force_password_reset'"
        )
    )
    if result.scalar_one_or_none() is None:
        pytest.skip("Run p53_force_password_reset migration (`uv run alembic upgrade head`).")


@pytest.mark.asyncio
async def test_create_org_with_tenant(db_session, users_force_reset_column, monkeypatch) -> None:
    clerk_org_id = f"org_{uuid4().hex[:12]}"

    async def mock_create_organization(name: str) -> dict[str, str]:
        return {"id": clerk_org_id, "name": name}

    async def mock_fetch_org(clerk_org_id: str) -> dict[str, str]:
        return {"id": clerk_org_id, "name": "Org Test"}

    monkeypatch.setattr(
        "app.services.admin_service.clerk_client.create_organization",
        mock_create_organization,
    )
    monkeypatch.setattr("app.services.auth_service.fetch_clerk_org", mock_fetch_org)

    tenant = await admin_service.create_org_with_tenant(db_session, "Org Test")
    assert tenant.clerk_org_id == clerk_org_id
    assert tenant.name == "Org Test"

    result = await db_session.execute(select(Tenant).where(Tenant.id == tenant.id))
    assert result.scalar_one() is not None


@pytest.mark.asyncio
async def test_create_user_in_org_sets_force_password_reset(
    db_session, users_force_reset_column, monkeypatch
) -> None:
    clerk_org_id = f"org_{uuid4().hex[:12]}"
    clerk_user_id = f"user_{uuid4().hex[:12]}"
    tenant = Tenant(clerk_org_id=clerk_org_id, name="T", plan="free")
    db_session.add(tenant)
    await db_session.flush()

    async def mock_create_user(**kwargs: object) -> dict[str, str]:
        return {"id": clerk_user_id}

    async def mock_add_org_member(
        org_id: str, user_id: str, role: str = "org:member"
    ) -> dict[str, str]:
        return {"id": "mem_1"}

    async def mock_fetch_user(clerk_user_id: str) -> dict[str, object]:
        return {
            "id": clerk_user_id,
            "email_addresses": [{"id": "e1", "email_address": "new@example.com"}],
            "primary_email_address_id": "e1",
            "first_name": "New",
            "last_name": "User",
        }

    monkeypatch.setattr("app.services.admin_service.clerk_client.create_user", mock_create_user)
    monkeypatch.setattr(
        "app.services.admin_service.clerk_client.add_org_member", mock_add_org_member
    )
    monkeypatch.setattr("app.services.auth_service.fetch_clerk_user", mock_fetch_user)

    user = await admin_service.create_user_in_org(
        db_session,
        "new@example.com",
        "New",
        "User",
        tenant.id,
        role="member",
    )
    assert user.force_password_reset is True

    result = await db_session.execute(select(User).where(User.id == user.id))
    db_user = result.scalar_one()
    assert db_user.force_password_reset is True

    result = await db_session.execute(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.tenant_id == tenant.id,
        )
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_create_user_rollback_on_db_error(
    db_session, users_force_reset_column, monkeypatch
) -> None:
    clerk_org_id = f"org_{uuid4().hex[:12]}"
    clerk_user_id = f"user_{uuid4().hex[:12]}"
    tenant = Tenant(clerk_org_id=clerk_org_id, name="T", plan="free")
    db_session.add(tenant)
    await db_session.flush()

    delete_user_called = False

    async def mock_create_user(**kwargs: object) -> dict[str, str]:
        return {"id": clerk_user_id}

    async def mock_add_org_member(
        org_id: str, user_id: str, role: str = "org:member"
    ) -> dict[str, str]:
        return {"id": "mem_1"}

    async def mock_delete_user(user_id: str) -> None:
        nonlocal delete_user_called
        delete_user_called = True

    async def mock_fetch_user(clerk_user_id: str) -> dict[str, object]:
        return {
            "id": clerk_user_id,
            "email_addresses": [{"id": "e1", "email_address": "fail@example.com"}],
            "primary_email_address_id": "e1",
        }

    async def mock_ensure_membership(*args: object, **kwargs: object) -> None:
        raise RuntimeError("db flush failed")

    monkeypatch.setattr("app.services.admin_service.clerk_client.create_user", mock_create_user)
    monkeypatch.setattr(
        "app.services.admin_service.clerk_client.add_org_member", mock_add_org_member
    )
    monkeypatch.setattr("app.services.admin_service.clerk_client.delete_user", mock_delete_user)
    monkeypatch.setattr("app.services.auth_service.fetch_clerk_user", mock_fetch_user)
    monkeypatch.setattr("app.services.admin_service.ensure_membership", mock_ensure_membership)

    with pytest.raises(RuntimeError, match="db flush failed"):
        await admin_service.create_user_in_org(
            db_session,
            "fail@example.com",
            "",
            "",
            tenant.id,
        )
    assert delete_user_called is True


@pytest.mark.asyncio
async def test_remove_user_from_org(db_session, users_force_reset_column, monkeypatch) -> None:
    clerk_org_id = f"org_{uuid4().hex[:12]}"
    tenant = Tenant(clerk_org_id=clerk_org_id, name="T", plan="free")
    user = User(
        clerk_user_id=f"user_{uuid4().hex[:12]}",
        email=f"{uuid4().hex}@example.com",
        name="U",
    )
    db_session.add(tenant)
    db_session.add(user)
    await db_session.flush()

    from app.core.db import set_tenant_context

    await set_tenant_context(db_session, str(tenant.id))
    membership = Membership(user_id=user.id, tenant_id=tenant.id, role="member")
    db_session.add(membership)
    await db_session.flush()

    remove_called = False

    async def mock_remove_org_member(org_id: str, user_id: str) -> None:
        nonlocal remove_called
        remove_called = True
        assert org_id == clerk_org_id
        assert user_id == user.clerk_user_id

    monkeypatch.setattr(
        "app.services.admin_service.clerk_client.remove_org_member",
        mock_remove_org_member,
    )

    await admin_service.remove_user_from_org(db_session, user.id, tenant.id)
    assert remove_called is True

    result = await db_session.execute(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.tenant_id == tenant.id,
        )
    )
    stored = result.scalar_one()
    assert stored.is_active is False
