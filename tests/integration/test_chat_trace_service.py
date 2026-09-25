"""Servicio SADM de trazas de chat (cross-tenant)."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.core.errors import NotFoundError
from app.models import (
    AuditLog,
    ChatMessage,
    ChatMessageRole,
    ChatThread,
    LLMCall,
    Tenant,
    User,
)
from app.services import chat_trace_service
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _ensure_superadmin_policies(db: AsyncSession) -> None:
    """Idempotente: aplica p61 si el entorno de test aún no tiene las policies."""
    for table in ("chat_threads", "chat_messages", "audit_log"):
        exists = (
            await db.execute(
                text(
                    "SELECT 1 FROM pg_policies "
                    "WHERE schemaname = 'public' AND tablename = :t AND policyname = 'superadmin_select'"
                ),
                {"t": table},
            )
        ).scalar_one_or_none()
        if exists:
            continue
        await db.execute(
            text(
                f"""
                CREATE POLICY superadmin_select ON {table}
                AS PERMISSIVE
                FOR SELECT
                USING (current_setting('app.superadmin_lookup', true) = 'true')
                """
            )
        )
    await db.commit()


async def test_list_and_detail_cross_tenant_trace(
    chat_schema_ready: None,
    audit_schema_ready: None,
    llm_calls_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    await _ensure_superadmin_policies(db_session)

    tenant_a = await tenant_factory(name="Org A")
    tenant_b = await tenant_factory(name="Org B")

    user = User(
        clerk_user_id=f"user_{uuid4().hex[:12]}",
        email="trace@test.local",
        name="Trace User",
    )
    db_session.add(user)
    await db_session.flush()

    await set_tenant_context(db_session, str(tenant_a.id))
    thread = ChatThread(
        tenant_id=tenant_a.id,
        user_id=user.id,
        title="factura ugars",
    )
    db_session.add(thread)
    await db_session.flush()

    t0 = datetime.now(tz=UTC)
    user_msg = ChatMessage(
        thread_id=thread.id,
        tenant_id=tenant_a.id,
        role=ChatMessageRole.user,
        content="qué facturas tengo?",
        created_at=t0,
    )
    db_session.add(user_msg)
    await db_session.flush()

    llm = LLMCall(
        tenant_id=tenant_a.id,
        task="chat",
        model="gemini-2.5-flash",
        provider="google",
        prompt_version="chat_unified_v1",
        input_tokens=120,
        output_tokens=40,
        cost_eur=Decimal("0.000100"),
        latency_ms=800,
        status="ok",
        langfuse_trace_id="lf-trace-test",
    )
    db_session.add(llm)
    await db_session.flush()

    tool_asst = ChatMessage(
        thread_id=thread.id,
        tenant_id=tenant_a.id,
        role=ChatMessageRole.assistant,
        content=None,
        tool_call={"calls": [{"name": "search_invoices", "id": "c1"}]},
        llm_call_id=llm.id,
        created_at=t0 + timedelta(microseconds=1),
    )
    tool_msg = ChatMessage(
        thread_id=thread.id,
        tenant_id=tenant_a.id,
        role=ChatMessageRole.tool,
        content=None,
        tool_call={"id": "c1", "name": "search_invoices"},
        tool_result={"ok": True, "count": 1},
        created_at=t0 + timedelta(microseconds=2),
    )
    final = ChatMessage(
        thread_id=thread.id,
        tenant_id=tenant_a.id,
        role=ChatMessageRole.assistant,
        content="Tienes 1 factura.",
        llm_call_id=llm.id,
        created_at=t0 + timedelta(microseconds=3),
    )
    db_session.add_all([tool_asst, tool_msg, final])
    db_session.add(
        AuditLog(
            tenant_id=tenant_a.id,
            user_id=user.id,
            action="chat.tool_executed",
            resource_type="chat_thread",
            resource_id=thread.id,
            metadata_={
                "thread_id": str(thread.id),
                "tool_name": "search_invoices",
                "ok": True,
                "llm_call_id": str(llm.id),
            },
        )
    )
    await db_session.flush()

    # Hilo en otro tenant (no debe mezclarse en el detalle).
    await set_tenant_context(db_session, str(tenant_b.id))
    other = ChatThread(tenant_id=tenant_b.id, user_id=None, title="otro")
    db_session.add(other)
    await db_session.flush()

    # Sin tenant en sesión: listado SADM.
    await db_session.execute(text("SELECT set_config('app.current_tenant', '', true)"))
    threads = await chat_trace_service.list_threads(db_session, limit=50)
    ids = {item.id for item in threads}
    assert thread.id in ids
    assert other.id in ids
    item = next(i for i in threads if i.id == thread.id)
    assert item.tenant_name == "Org A"
    assert item.user_email == "trace@test.local"
    assert item.message_count == 4

    detail = await chat_trace_service.get_thread_trace(db_session, thread_id=thread.id)
    assert detail.thread.id == thread.id
    assert [m.role for m in detail.messages] == [
        ChatMessageRole.user,
        ChatMessageRole.assistant,
        ChatMessageRole.tool,
        ChatMessageRole.assistant,
    ]
    assert detail.messages[0].content == "qué facturas tengo?"
    assert detail.messages[1].content is None
    assert detail.messages[1].tool_call is not None
    assert detail.messages[1].llm_call is not None
    assert detail.messages[1].llm_call.model == "gemini-2.5-flash"
    assert detail.messages[3].content == "Tienes 1 factura."
    assert len(detail.llm_calls) >= 1
    assert any(c.id == llm.id for c in detail.llm_calls)
    assert any(e.action == "chat.tool_executed" for e in detail.audit_events)


async def test_get_thread_trace_not_found(
    chat_schema_ready: None,
    audit_schema_ready: None,
    llm_calls_schema_ready: None,
    db_session: AsyncSession,
) -> None:
    await _ensure_superadmin_policies(db_session)
    await db_session.execute(text("SELECT set_config('app.current_tenant', '', true)"))
    with pytest.raises(NotFoundError):
        await chat_trace_service.get_thread_trace(db_session, thread_id=uuid4())
