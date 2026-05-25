"""Integración: flujo chat thread → mensaje → tool loop mock → respuesta persistida."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.llm.chat_loop import ToolLoopResult, TurnMessageRecord
from app.models import ChatMessage, ChatMessageRole, ChatThread, Tenant, User
from app.services import chat_service
from sqlalchemy import select

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.mark.asyncio
async def test_chat_turn_persists_assistant_after_tool_loop(
    chat_schema_ready: None,
    audit_schema_ready: None,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = Tenant(name="Chat flow tenant")
    db_session.add(tenant)
    user = User(email=f"flow-{uuid4().hex[:8]}@test.local", name="Flow User")
    db_session.add(user)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))

    thread = ChatThread(tenant_id=tenant.id, user_id=user.id, title="Test")
    db_session.add(thread)
    await db_session.flush()

    user_msg = ChatMessage(
        tenant_id=tenant.id,
        thread_id=thread.id,
        role=ChatMessageRole.user,
        content="¿Cuántas facturas hay?",
    )
    db_session.add(user_msg)
    await db_session.flush()

    async def fake_loop(**_kwargs: object) -> ToolLoopResult:
        return ToolLoopResult(
            final_text="Hay 3 facturas en el periodo consultado.",
            llm_call_ids=[uuid4()],
            tool_calls_executed=["aggregate_documents"],
            turn_messages=(
                TurnMessageRecord(
                    role="assistant",
                    content="",
                    tool_call={
                        "calls": [{"id": "c1", "name": "aggregate_documents", "arguments": {}}]
                    },
                ),
                TurnMessageRecord(
                    role="tool",
                    tool_call={"id": "c1", "name": "aggregate_documents", "arguments": {}},
                    tool_result={"ok": True, "data": {"total_value": 3}},
                ),
                TurnMessageRecord(
                    role="assistant",
                    content="Hay 3 facturas en el periodo consultado.",
                ),
            ),
        )

    mock_client = MagicMock()
    mock_client.run_tool_loop = fake_loop
    monkeypatch.setattr("app.services.chat_service.get_llm_client", lambda: mock_client)

    chunks: list[str] = []
    async for part in chat_service._run_assistant_turn(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread.id,
    ):
        chunks.append(part)

    assert "".join(chunks) == "Hay 3 facturas en el periodo consultado."

    result = await db_session.execute(
        select(ChatMessage).where(
            ChatMessage.thread_id == thread.id,
            ChatMessage.role == ChatMessageRole.assistant,
        ),
    )
    assistant_rows = list(result.scalars().all())
    assert len(assistant_rows) >= 1
    final_assistant = [m for m in assistant_rows if m.content and "3 facturas" in m.content]
    assert len(final_assistant) == 1

    tool_rows = await db_session.execute(
        select(ChatMessage).where(
            ChatMessage.thread_id == thread.id,
            ChatMessage.role == ChatMessageRole.tool,
        ),
    )
    assert len(tool_rows.scalars().all()) == 1


@pytest.mark.asyncio
async def test_create_thread_and_post_message_integration(
    chat_schema_ready: None,
    audit_schema_ready: None,
    db_session,
) -> None:
    tenant = Tenant(name="Chat create tenant")
    db_session.add(tenant)
    user = User(email=f"create-{uuid4().hex[:8]}@test.local")
    db_session.add(user)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))

    thread_read = await chat_service.create_thread(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
    )

    redis_conn = AsyncMock()
    redis_conn.incr = AsyncMock(return_value=1)
    redis_conn.expire = AsyncMock()

    msg_read = await chat_service.post_user_message(
        db_session,
        redis_conn,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread_read.id,
        content="Hola",
    )
    assert msg_read.role == ChatMessageRole.user

    thread = await chat_service.get_thread(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread_read.id,
    )
    assert thread.title == "Hola"
