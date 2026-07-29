"""Tests unitarios del servicio de chat."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.core.errors import ForbiddenError, RateLimitError, ValidationError
from app.llm.chat_loop import ToolLoopResult, TurnMessageRecord
from app.models import ChatThread, User
from app.services import chat_service


def test_validate_message_content_rejects_empty() -> None:
    with pytest.raises(ValidationError, match="vacío"):
        chat_service.validate_message_content("   ")


def test_validate_message_content_rejects_oversized() -> None:
    huge = "x" * 5000
    with pytest.raises(ValidationError, match="límite"):
        chat_service.validate_message_content(huge)


@pytest.mark.asyncio
async def test_enforce_rate_limit_raises_when_exceeded() -> None:
    redis_conn = AsyncMock()
    redis_conn.incr = AsyncMock(return_value=61)
    redis_conn.expire = AsyncMock()
    with pytest.raises(RateLimitError, match="límite diario"):
        await chat_service.enforce_rate_limit(
            redis_conn,
            tenant_id=uuid4(),
            user_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_get_thread_forbidden_wrong_user(
    chat_schema_ready: None,
    db_session,
) -> None:
    from app.core.db import set_tenant_context
    from app.models import Tenant

    tenant = Tenant(name="Chat tenant")
    db_session.add(tenant)
    owner = User(email=f"owner-{uuid4().hex[:8]}@test.local", name="Owner")
    other = User(email=f"other-{uuid4().hex[:8]}@test.local", name="Other")
    db_session.add_all([owner, other])
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))

    thread = ChatThread(tenant_id=tenant.id, user_id=owner.id, title="Hilo")
    db_session.add(thread)
    await db_session.flush()

    with pytest.raises(ForbiddenError):
        await chat_service.get_thread(
            db_session,
            tenant_id=tenant.id,
            user_id=other.id,
            thread_id=thread.id,
        )


@pytest.mark.asyncio
async def test_hide_thread_soft_hides_without_deleting(
    chat_schema_ready: None,
    db_session,
) -> None:
    from app.core.db import set_tenant_context
    from app.core.errors import NotFoundError
    from app.models import ChatMessage, ChatMessageRole, Tenant
    from app.schemas.chat import ChatThreadListFilters
    from sqlalchemy import func, select

    tenant = Tenant(name="Hide thread tenant")
    db_session.add(tenant)
    user = User(email=f"hide-{uuid4().hex[:8]}@test.local", name="Hide")
    db_session.add(user)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))

    thread = ChatThread(tenant_id=tenant.id, user_id=user.id, title="Visible")
    db_session.add(thread)
    await db_session.flush()
    db_session.add(
        ChatMessage(
            thread_id=thread.id,
            tenant_id=tenant.id,
            role=ChatMessageRole.user,
            content="hola",
        )
    )
    await db_session.flush()

    await chat_service.hide_thread(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread.id,
    )

    page = await chat_service.list_threads(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        filters=ChatThreadListFilters(),
    )
    assert page.total == 0
    assert page.items == []

    with pytest.raises(NotFoundError):
        await chat_service.get_thread(
            db_session,
            tenant_id=tenant.id,
            user_id=user.id,
            thread_id=thread.id,
        )

    # Filas conservadas en BD
    still = await db_session.get(ChatThread, thread.id)
    assert still is not None
    assert still.is_hidden is True
    msg_count = int(
        (
            await db_session.execute(
                select(func.count())
                .select_from(ChatMessage)
                .where(ChatMessage.thread_id == thread.id)
            )
        ).scalar_one()
    )
    assert msg_count == 1


@pytest.mark.asyncio
async def test_post_user_message_persists(
    chat_schema_ready: None,
    audit_schema_ready: None,
    db_session,
) -> None:
    from app.core.db import set_tenant_context
    from app.models import Tenant

    tenant = Tenant(name="Post msg tenant")
    db_session.add(tenant)
    user = User(email=f"post-{uuid4().hex[:8]}@test.local")
    db_session.add(user)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    thread = ChatThread(tenant_id=tenant.id, user_id=user.id)
    db_session.add(thread)
    await db_session.flush()

    redis_conn = AsyncMock()
    redis_conn.incr = AsyncMock(return_value=1)
    redis_conn.expire = AsyncMock()

    from app.models import AuditLog
    from app.services.audit_service import ACTION_CHAT_MESSAGE_SENT, AuditRequestContext
    from sqlalchemy import select

    read = await chat_service.post_user_message(
        db_session,
        redis_conn,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread.id,
        content="  Hola chat  ",
        request_ctx=AuditRequestContext(ip="10.0.0.1"),
    )
    assert read.content == "Hola chat"
    assert read.role.value == "user"

    audit_rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant.id,
                    AuditLog.action == ACTION_CHAT_MESSAGE_SENT,
                ),
            )
        )
        .scalars()
        .all()
    )
    assert len(audit_rows) == 1
    assert audit_rows[0].resource_id == read.id


@pytest.mark.asyncio
async def test_send_message_yields_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models import ChatMessageRole
    from app.schemas.chat import ChatMessageRead

    async def fake_turn(*_args: object, **_kwargs: object) -> AsyncIterator[str]:
        yield "Hola "
        yield "mundo"

    fake_read = ChatMessageRead(
        id=uuid4(),
        thread_id=uuid4(),
        tenant_id=uuid4(),
        role=ChatMessageRole.user,
        content="test",
        created_at=datetime.now(tz=UTC),
    )

    monkeypatch.setattr(chat_service, "_run_assistant_turn", fake_turn)
    monkeypatch.setattr(chat_service, "post_user_message", AsyncMock(return_value=fake_read))

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    chunks: list[str] = []
    async for part in chat_service.send_message(
        db,
        AsyncMock(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        thread_id=uuid4(),
        content="¿Total de tickets?",
    ):
        chunks.append(part)

    assert chunks == ["Hola ", "mundo"]


@pytest.mark.asyncio
async def test_run_assistant_turn_yields_chunked_reply(
    chat_schema_ready: None,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.db import set_tenant_context
    from app.models import Tenant

    tenant = Tenant(name="Turn tenant")
    db_session.add(tenant)
    user = User(email=f"u-{uuid4().hex[:8]}@test.local")
    db_session.add(user)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    thread = ChatThread(tenant_id=tenant.id, user_id=user.id)
    db_session.add(thread)
    await db_session.flush()

    mock_client = MagicMock()

    async def fake_loop(**_kwargs: object) -> ToolLoopResult:
        return ToolLoopResult(
            final_text="Respuesta",
            llm_call_ids=[],
            tool_calls_executed=[],
            turn_messages=(TurnMessageRecord(role="assistant", content="Respuesta"),),
        )

    mock_client.run_tool_loop = fake_loop
    monkeypatch.setattr(
        "app.services.chat_service.get_llm_client",
        lambda: mock_client,
    )

    chunks: list[str] = []
    async for part in chat_service._run_assistant_turn(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread.id,
    ):
        chunks.append(part)

    assert "".join(chunks) == "Respuesta"
