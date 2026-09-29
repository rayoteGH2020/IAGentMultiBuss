"""Email al admin por el presupuesto de IA, contra Postgres real (RLS)."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.models import Membership, Tenant, User
from app.services import llm_budget_alert_service, usage_meter_service
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _user(db: AsyncSession, email: str) -> User:
    user = User(clerk_user_id=f"user_{uuid4().hex[:12]}", email=email, name="U")
    db.add(user)
    await db.flush()
    return user


async def test_alert_goes_to_active_admin_only(
    usage_meter_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await tenant_factory(name="Org Presupuesto")
    admin = await _user(db_session, f"admin-{uuid4().hex[:6]}@test.local")
    co_admin = await _user(db_session, f"co-{uuid4().hex[:6]}@test.local")
    await set_tenant_context(db_session, str(tenant.id))
    db_session.add_all(
        [
            Membership(user_id=admin.id, tenant_id=tenant.id, role="admin", is_active=True),
            Membership(user_id=co_admin.id, tenant_id=tenant.id, role="co_admin", is_active=True),
        ]
    )
    usage = await llm_budget_alert_service.get_budget_usage(db_session, tenant.id)
    assert usage.budget is not None
    await usage_meter_service.add_llm_cost_eur(
        db_session, tenant_id=tenant.id, delta=usage.budget * Decimal("0.82")
    )
    await db_session.flush()
    sent = AsyncMock()
    monkeypatch.setattr(llm_budget_alert_service, "send_email", sent)

    assert await llm_budget_alert_service.send_admin_alert(db_session, tenant.id, "budget_warning")

    sent.assert_awaited_once()
    kwargs = sent.await_args.kwargs
    assert kwargs["to"] == admin.email
    assert "82 %" in kwargs["subject"]
    assert "Org Presupuesto" in kwargs["body"]


async def test_alert_without_active_admin_is_skipped(
    usage_meter_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await tenant_factory()
    former = await _user(db_session, f"old-{uuid4().hex[:6]}@test.local")
    await set_tenant_context(db_session, str(tenant.id))
    db_session.add(
        Membership(user_id=former.id, tenant_id=tenant.id, role="admin", is_active=False)
    )
    await db_session.flush()
    sent = AsyncMock()
    monkeypatch.setattr(llm_budget_alert_service, "send_email", sent)

    assert not await llm_budget_alert_service.send_admin_alert(db_session, tenant.id, "chat_cutoff")
    sent.assert_not_awaited()


async def test_sadm_alert_carries_tenant_and_admin_contact(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    tenant = await tenant_factory(name="Clinica Sol")
    admin = await _user(db_session, f"ana-{uuid4().hex[:6]}@test.local")
    await set_tenant_context(db_session, str(tenant.id))
    db_session.add(Membership(user_id=admin.id, tenant_id=tenant.id, role="admin", is_active=True))
    await db_session.flush()

    monkeypatch.setattr(
        llm_budget_alert_service,
        "get_settings",
        lambda: SimpleNamespace(email_sadm="sadm@test.local", chat_budget_cutoff_ratio=0.9),
    )
    monkeypatch.setattr(
        llm_budget_alert_service.clerk_client,
        "get_user",
        AsyncMock(
            return_value={
                "first_name": "Ana",
                "last_name": "Garcia",
                "primary_phone_number_id": "p1",
                "phone_numbers": [{"id": "p1", "phone_number": "+34600111222"}],
            }
        ),
    )
    sent = AsyncMock()
    monkeypatch.setattr(llm_budget_alert_service, "send_email", sent)

    assert await llm_budget_alert_service.send_sadm_alert(db_session, tenant.id)

    kwargs = sent.await_args.kwargs
    assert kwargs["to"] == "sadm@test.local"
    assert kwargs["subject"] == "Cuota de uso de IA de uno de los tenant al 90%"
    assert kwargs["body"] == (
        f"El tenant Clinica Sol-{tenant.id}, ha llegado al 90% de su cupo de uso de IA para "
        "este mes. Contacta con su admin para gestionarlo.\n"
        f"Admin del tenant: Ana, Garcia, {admin.email} y +34600111222."
    )


async def test_sadm_alert_skipped_without_sadm_email(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    tenant = await tenant_factory()
    monkeypatch.setattr(
        llm_budget_alert_service,
        "get_settings",
        lambda: SimpleNamespace(email_sadm=" ", chat_budget_cutoff_ratio=0.9),
    )
    sent = AsyncMock()
    monkeypatch.setattr(llm_budget_alert_service, "send_email", sent)

    assert not await llm_budget_alert_service.send_sadm_alert(db_session, tenant.id)
    sent.assert_not_awaited()
