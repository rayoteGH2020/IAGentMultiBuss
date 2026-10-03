"""Integración: cupo mensual de preguntas del chat y límite de ritmo (bloque 4, D023)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.core.entitlement_codes import LIMIT_CHAT_QUESTIONS_PER_MONTH
from app.core.errors import RateLimitError
from app.llm.chat_loop import ToolLoopResult, TurnMessageRecord
from app.models import ChatMessage, ChatMessageRole, ChatThread, Tenant, User
from app.schemas.entitlements import Entitlements
from app.services import chat_service, monthly_quota_service
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _ents(questions: int) -> Entitlements:
    return Entitlements(
        plan_code="basic",
        features=frozenset({"documents_chat"}),
        limits={LIMIT_CHAT_QUESTIONS_PER_MONTH: Decimal(questions)},
    )


async def _thread(db: AsyncSession) -> tuple[Tenant, User, ChatThread]:
    tenant = Tenant(name=f"Chat quota {uuid4().hex[:8]}", plan_code="basic")
    user = User(email=f"chat-{uuid4().hex[:8]}@test.local", phone="600111222")
    db.add_all([tenant, user])
    await db.flush()
    await set_tenant_context(db, str(tenant.id))
    thread = ChatThread(tenant_id=tenant.id, user_id=user.id)
    db.add(thread)
    await db.flush()
    return tenant, user, thread


def _loop(*, failed: bool = False) -> MagicMock:
    text = "No he podido responder." if failed else "Tienes 3 facturas."
    client = MagicMock()
    client.run_tool_loop = AsyncMock(
        return_value=ToolLoopResult(
            final_text=text,
            llm_call_ids=[],
            tool_calls_executed=[],
            turn_messages=(TurnMessageRecord(role="assistant", content=text),),
            failed=failed,
        )
    )
    return client


@pytest.fixture
def chat_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    env: dict[str, Any] = {"ents": _ents(2), "client": _loop()}

    async def _resolve(_db: AsyncSession, _tenant_id: Any) -> Entitlements:
        return env["ents"]

    monkeypatch.setattr("app.services.chat_service.entitlement_service.resolve_tenant", _resolve)
    monkeypatch.setattr("app.services.chat_service.get_llm_client", lambda: env["client"])
    monkeypatch.setattr(
        "app.services.chat_service.llm_budget_alert_service.chat_cutoff_reached",
        AsyncMock(return_value=False),
    )
    return env


async def _turn(db: AsyncSession, tenant: Tenant, user: User, thread: ChatThread) -> str:
    db.add(
        ChatMessage(
            tenant_id=tenant.id, thread_id=thread.id, role=ChatMessageRole.user, content="¿Y?"
        )
    )
    await db.flush()
    parts = [
        part
        async for part in chat_service._run_assistant_turn(
            db, tenant_id=tenant.id, user_id=user.id, thread_id=thread.id
        )
    ]
    return "".join(parts)


async def _used(db: AsyncSession, tenant: Tenant) -> int:
    usage = await monthly_quota_service.get_usage(db, _ents(2), tenant.id)
    return usage[LIMIT_CHAT_QUESTIONS_PER_MONTH].used


async def test_each_answered_turn_consumes_one_question_until_the_cap(
    db_session: AsyncSession,
    chat_schema_ready: None,
    audit_schema_ready: None,
    chat_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, user, thread = await _thread(db_session)
    monkeypatch.setattr(
        "app.services.chat_service.llm_budget_alert_service.tenant_admin",
        AsyncMock(return_value=user),
    )

    assert await _turn(db_session, tenant, user, thread) == "Tienes 3 facturas."
    assert await _turn(db_session, tenant, user, thread) == "Tienes 3 facturas."
    exhausted = await _turn(db_session, tenant, user, thread)

    assert chat_env["client"].run_tool_loop.await_count == 2
    assert await _used(db_session, tenant) == 2
    assert exhausted.startswith("Has alcanzado las preguntas de este mes. Se renuevan el 1 de")
    assert f"(600111222 - {user.email})" in exhausted
    last = (
        await db_session.execute(
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
    ).scalar_one()
    assert last.role == ChatMessageRole.assistant and last.content == exhausted


async def test_provider_failure_returns_the_question(
    db_session: AsyncSession,
    chat_schema_ready: None,
    audit_schema_ready: None,
    chat_env: dict[str, Any],
) -> None:
    chat_env["client"] = _loop(failed=True)
    tenant, user, thread = await _thread(db_session)

    assert await _turn(db_session, tenant, user, thread) == "No he podido responder."

    assert await _used(db_session, tenant) == 0


async def test_budget_cutoff_does_not_consume_questions(
    db_session: AsyncSession,
    chat_schema_ready: None,
    audit_schema_ready: None,
    chat_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.chat_service.llm_budget_alert_service.chat_cutoff_reached",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "app.services.chat_service.llm_budget_alert_service.notify_chat_cutoff", AsyncMock()
    )
    tenant, user, thread = await _thread(db_session)

    reply = await _turn(db_session, tenant, user, thread)

    assert "no puedo responderte" in reply
    assert await _used(db_session, tenant) == 0
    chat_env["client"].run_tool_loop.assert_not_awaited()


async def test_sadm_extension_reopens_the_chat_this_month(
    db_session: AsyncSession,
    chat_schema_ready: None,
    audit_schema_ready: None,
    chat_env: dict[str, Any],
) -> None:
    chat_env["ents"] = _ents(1)
    tenant, user, thread = await _thread(db_session)
    await _turn(db_session, tenant, user, thread)
    assert (await _turn(db_session, tenant, user, thread)).startswith("Has alcanzado")

    await monthly_quota_service.add_extra(
        db_session,
        tenant_id=tenant.id,
        code=LIMIT_CHAT_QUESTIONS_PER_MONTH,
        amount=1,
        actor_user_id=None,
    )

    assert await _turn(db_session, tenant, user, thread) == "Tienes 3 facturas."
    assert chat_env["client"].run_tool_loop.await_count == 2


async def test_rate_limit_blocks_before_saving_the_message(
    db_session: AsyncSession,
    chat_schema_ready: None,
    audit_schema_ready: None,
) -> None:
    tenant, user, thread = await _thread(db_session)
    redis = AsyncMock()
    redis.incrby = AsyncMock(return_value=11)  # 11.ª pregunta en el mismo minuto
    redis.expire = AsyncMock()
    redis.decrby = AsyncMock()

    with pytest.raises(RateLimitError, match="muy seguidas"):
        await chat_service.post_user_message(
            db_session,
            redis,
            tenant_id=tenant.id,
            user_id=user.id,
            thread_id=thread.id,
            content="Hola",
        )

    count = await db_session.scalar(
        select(func.count()).select_from(ChatMessage).where(ChatMessage.thread_id == thread.id)
    )
    assert count == 0
