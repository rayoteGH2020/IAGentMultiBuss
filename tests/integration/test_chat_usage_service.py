"""Integración: agregación SADM de uso de chat por tenant."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.models import ChatMessage, ChatMessageRole, ChatThread, LLMCall, Tenant, User
from app.services import chat_usage_service
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_get_tenant_chat_usage_aggregates_by_month(
    chat_schema_ready: None,
    llm_calls_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.chat_usage_service._today",
        lambda: date(2026, 7, 28),
    )
    tenant = await tenant_factory(name="Chat Usage Org")
    other = await tenant_factory(name="Other Org")
    user = User(
        clerk_user_id=f"user_{uuid4().hex[:12]}",
        email="chat-usage@test.local",
        name="CU",
    )
    db_session.add(user)
    await db_session.flush()

    await set_tenant_context(db_session, str(tenant.id))
    thread = ChatThread(tenant_id=tenant.id, user_id=user.id, title="uso")
    db_session.add(thread)
    await db_session.flush()

    day = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    db_session.add(
        ChatMessage(
            thread_id=thread.id,
            tenant_id=tenant.id,
            role=ChatMessageRole.user,
            content="hola",
            created_at=day,
        )
    )
    db_session.add(
        LLMCall(
            tenant_id=tenant.id,
            task="chat",
            model="gemini-2.5-flash",
            provider="google",
            input_tokens=100,
            output_tokens=50,
            cost_eur=Decimal("0.010000"),
            latency_ms=500,
            status="ok",
            created_at=day,
        )
    )
    db_session.add(
        LLMCall(
            tenant_id=tenant.id,
            task="chat",
            model="gemini-2.5-flash",
            provider="google",
            input_tokens=40,
            output_tokens=20,
            cost_eur=Decimal("0.005000"),
            latency_ms=300,
            status="ok",
            created_at=day + timedelta(hours=1),
        )
    )
    db_session.add(
        LLMCall(
            tenant_id=tenant.id,
            task="extraction",
            model="gemini-2.5-flash",
            provider="google",
            input_tokens=999,
            output_tokens=999,
            cost_eur=Decimal("1.000000"),
            latency_ms=10,
            status="ok",
            created_at=day,
        )
    )
    await db_session.flush()

    await set_tenant_context(db_session, str(other.id))
    db_session.add(
        LLMCall(
            tenant_id=other.id,
            task="chat",
            model="gemini-2.5-flash",
            provider="google",
            input_tokens=10,
            output_tokens=10,
            cost_eur=Decimal("0.001000"),
            latency_ms=100,
            status="ok",
            created_at=day,
        )
    )
    await db_session.flush()
    await db_session.execute(text("SELECT set_config('app.current_tenant', '', true)"))

    report = await chat_usage_service.get_tenant_chat_usage(
        db_session,
        tenant_id=tenant.id,
        view="month",
        anchor=date(2026, 7, 1),
        sort="asc",
    )
    assert report.tenant_name == "Chat Usage Org"
    assert len(report.rows) == 6
    assert [r.period_label for r in report.rows] == [
        "2026-02",
        "2026-03",
        "2026-04",
        "2026-05",
        "2026-06",
        "2026-07",
    ]
    row = next(r for r in report.rows if r.period_date == date(2026, 7, 1))
    assert row.period_label == "2026-07"
    assert row.chat_count == 1
    assert row.llm_calls == 2
    assert row.input_tokens == 140
    assert row.output_tokens == 70
    assert row.cost_eur == Decimal("0.015000")
    assert row.avg_latency_ms == pytest.approx(400.0)
    assert report.totals.cost_eur >= row.cost_eur


async def test_list_active_tenants_ordered(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    await tenant_factory(name="Zeta Org")
    await tenant_factory(name="Alpha Org")
    tenants = await chat_usage_service.list_active_tenants(db_session)
    names = [t.name for t in tenants]
    assert "Alpha Org" in names
    assert names.index("Alpha Org") < names.index("Zeta Org")
