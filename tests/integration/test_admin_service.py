"""Tests de admin_service read-only (Paso05)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.user import User
from app.services import admin_service

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_list_all_tenants_returns_multiple(db_session) -> None:
    t1 = Tenant(name=f"A {uuid4().hex[:6]}", plan_code="basic", plan="basic")
    t2 = Tenant(name=f"B {uuid4().hex[:6]}", plan_code="basic", plan="basic")
    db_session.add_all([t1, t2])
    await db_session.flush()

    tenants = await admin_service.list_all_tenants(db_session)
    ids = {t.id for t in tenants}
    assert t1.id in ids
    assert t2.id in ids


@pytest.mark.asyncio
async def test_list_tenant_members_active_only(db_session) -> None:
    tenant = Tenant(name=f"M {uuid4().hex[:6]}", plan_code="basic", plan="basic")
    active = User(email=f"a-{uuid4().hex[:8]}@ex.com", name="Active")
    inactive = User(email=f"i-{uuid4().hex[:8]}@ex.com", name="Inactive")
    db_session.add_all([tenant, active, inactive])
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))

    db_session.add(Membership(user_id=active.id, tenant_id=tenant.id, role="admin", is_active=True))
    db_session.add(
        Membership(user_id=inactive.id, tenant_id=tenant.id, role="member", is_active=False)
    )
    await db_session.flush()

    members = await admin_service.list_tenant_members(db_session, tenant.id)
    emails = {u.email for u, _ in members}
    assert active.email in emails
    assert inactive.email not in emails


@pytest.mark.asyncio
async def test_get_tenant_not_found(db_session) -> None:
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await admin_service.get_tenant(db_session, uuid4())


@pytest.mark.asyncio
async def test_provision_helpers_removed() -> None:
    """Paso05: no deben existir APIs de provision en admin_service."""
    assert not hasattr(admin_service, "create_org_with_tenant")
    assert not hasattr(admin_service, "create_user_in_org")
    assert not hasattr(admin_service, "remove_user_from_org")
