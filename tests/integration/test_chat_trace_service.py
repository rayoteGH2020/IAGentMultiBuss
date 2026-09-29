"""Servicio SADM de trazas de chat: solo el tenant propio del superadmin."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

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
from app.services.audit_service import AuditRequestContext
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _make_user(db: AsyncSession, email: str) -> User:
    user = User(clerk_user_id=f"user_{uuid4().hex[:12]}", email=email, name="Trace User")
    db.add(user)
    await db.flush()
    return user


async def _seed_thread(db: AsyncSession, tenant: Tenant, user: User) -> tuple[ChatThread, LLMCall]:
    """Hilo con user → assistant(tool_call) → tool → assistant y un audit de tool."""
    await set_tenant_context(db, str(tenant.id))
    thread = ChatThread(tenant_id=tenant.id, user_id=user.id, title="factura ugars")
    db.add(thread)
    await db.flush()

    t0 = datetime.now(tz=UTC)
    db.add(
        ChatMessage(
            thread_id=thread.id,
            tenant_id=tenant.id,
            role=ChatMessageRole.user,
            content="qué facturas tengo?",
            created_at=t0,
        )
    )
    llm = LLMCall(
        tenant_id=tenant.id,
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
    db.add(llm)
    await db.flush()

    db.add_all(
        [
            ChatMessage(
                thread_id=thread.id,
                tenant_id=tenant.id,
                role=ChatMessageRole.assistant,
                content=None,
                tool_call={"calls": [{"name": "search_invoices", "id": "c1"}]},
                llm_call_id=llm.id,
                created_at=t0 + timedelta(microseconds=1),
            ),
            ChatMessage(
                thread_id=thread.id,
                tenant_id=tenant.id,
                role=ChatMessageRole.tool,
                content=None,
                tool_call={"id": "c1", "name": "search_invoices"},
                tool_result={"ok": True, "count": 1},
                created_at=t0 + timedelta(microseconds=2),
            ),
            ChatMessage(
                thread_id=thread.id,
                tenant_id=tenant.id,
                role=ChatMessageRole.assistant,
                content="Tienes 1 factura.",
                llm_call_id=llm.id,
                created_at=t0 + timedelta(microseconds=3),
            ),
            AuditLog(
                tenant_id=tenant.id,
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
            ),
        ]
    )
    await db.flush()
    return thread, llm


async def _other_tenant_thread(db: AsyncSession, tenant: Tenant) -> ChatThread:
    await set_tenant_context(db, str(tenant.id))
    other = ChatThread(tenant_id=tenant.id, user_id=None, title="otro")
    db.add(other)
    await db.flush()
    db.add(
        ChatMessage(
            thread_id=other.id,
            tenant_id=tenant.id,
            role=ChatMessageRole.user,
            content="dato privado de otro tenant",
            created_at=datetime.now(tz=UTC),
        )
    )
    await db.flush()
    return other


async def _viewed_count(db: AsyncSession, tenant_id: UUID, thread_id: UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.tenant_id == tenant_id,
                    AuditLog.action == chat_trace_service.ACTION_SADM_CHAT_TRACE_VIEWED,
                    AuditLog.resource_id == thread_id,
                )
            )
        ).scalar_one()
    )


async def test_list_and_detail_own_tenant_only(
    chat_schema_ready: None,
    audit_schema_ready: None,
    llm_calls_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    sadm_tenant = await tenant_factory(name="Org SADM")
    other_tenant = await tenant_factory(name="Org B")
    user = await _make_user(db_session, "trace@test.local")
    thread, llm = await _seed_thread(db_session, sadm_tenant, user)
    other = await _other_tenant_thread(db_session, other_tenant)

    # El request SADM llega con RLS de su propio tenant (get_db).
    await set_tenant_context(db_session, str(sadm_tenant.id))
    threads = await chat_trace_service.list_threads(db_session, tenant_id=sadm_tenant.id)
    ids = {item.id for item in threads}
    assert thread.id in ids
    assert other.id not in ids
    item = next(i for i in threads if i.id == thread.id)
    assert item.tenant_name == "Org SADM"
    assert item.user_email == "trace@test.local"
    assert item.message_count == 4

    detail = await chat_trace_service.get_thread_trace(
        db_session,
        tenant_id=sadm_tenant.id,
        thread_id=thread.id,
        viewer_id=user.id,
        request_ctx=AuditRequestContext(ip="203.0.113.9", user_agent="pytest"),
    )
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
    assert any(c.id == llm.id for c in detail.llm_calls)
    assert any(e.action == "chat.tool_executed" for e in detail.audit_events)
    assert await _viewed_count(db_session, sadm_tenant.id, thread.id) == 1


async def test_detail_of_other_tenant_thread_is_not_found(
    chat_schema_ready: None,
    audit_schema_ready: None,
    llm_calls_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    sadm_tenant = await tenant_factory(name="Org SADM")
    other_tenant = await tenant_factory(name="Org B")
    user = await _make_user(db_session, "sadm@test.local")
    other = await _other_tenant_thread(db_session, other_tenant)

    await set_tenant_context(db_session, str(sadm_tenant.id))
    with pytest.raises(NotFoundError):
        await chat_trace_service.get_thread_trace(
            db_session, tenant_id=sadm_tenant.id, thread_id=other.id, viewer_id=user.id
        )
    # Aunque se pidiera con el tenant ajeno, RLS de la sesión lo oculta.
    with pytest.raises(NotFoundError):
        await chat_trace_service.get_thread_trace(
            db_session, tenant_id=other_tenant.id, thread_id=other.id, viewer_id=user.id
        )
    await set_tenant_context(db_session, str(other_tenant.id))
    assert await _viewed_count(db_session, other_tenant.id, other.id) == 0


async def test_get_thread_trace_not_found(
    chat_schema_ready: None,
    audit_schema_ready: None,
    llm_calls_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    with pytest.raises(NotFoundError):
        await chat_trace_service.get_thread_trace(
            db_session, tenant_id=tenant.id, thread_id=uuid4(), viewer_id=uuid4()
        )


async def test_superadmin_lookup_flag_no_longer_exposes_chat(
    chat_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    """p72: ni con ``app.superadmin_lookup`` se leen chats de otro tenant (RLS en BD)."""
    sadm_tenant = await tenant_factory(name="Org SADM")
    other_tenant = await tenant_factory(name="Org B")
    other = await _other_tenant_thread(db_session, other_tenant)

    await set_tenant_context(db_session, str(sadm_tenant.id))
    await db_session.execute(text("SELECT set_config('app.superadmin_lookup', 'true', true)"))
    threads = (
        await db_session.execute(select(func.count(ChatThread.id)).where(ChatThread.id == other.id))
    ).scalar_one()
    messages = (
        await db_session.execute(
            select(func.count(ChatMessage.id)).where(ChatMessage.thread_id == other.id)
        )
    ).scalar_one()
    assert threads == 0
    assert messages == 0
