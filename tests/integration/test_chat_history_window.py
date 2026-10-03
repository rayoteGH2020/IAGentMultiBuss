"""Historial del chat: el corte nunca deja una cadena de tools a medias (Gemini 400).

Reproduce el fallo de 2026-10-03: con ``chat_history_message_limit`` = 20 y
preguntas que lanzan varias búsquedas (12-13 mensajes por turno), los últimos 20
mensajes empezaban en la respuesta de una tool sin su llamada y el proveedor
rechazaba la petición; el hilo quedaba roto y cada aviso de error lo empeoraba.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.core.db import set_tenant_context
from app.models import ChatMessage, ChatMessageRole, ChatThread, LLMCall, Tenant, User
from app.services import chat_service
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class _Thread:
    def __init__(self, db: AsyncSession, tenant_id: UUID, thread_id: UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.thread_id = thread_id
        self.ts = datetime.now(tz=UTC) - timedelta(hours=1)

    async def _llm_call(self, status: str) -> UUID:
        call = LLMCall(
            tenant_id=self.tenant_id, task="chat", model="m", provider="google", status=status
        )
        self.db.add(call)
        await self.db.flush()
        return call.id

    def _add(self, role: ChatMessageRole, **fields: Any) -> None:
        self.ts += timedelta(seconds=1)
        self.db.add(
            ChatMessage(
                tenant_id=self.tenant_id,
                thread_id=self.thread_id,
                role=role,
                created_at=self.ts,
                **fields,
            )
        )

    async def user(self, text: str) -> None:
        self._add(ChatMessageRole.user, content=text)

    async def tool_round(self, n_calls: int) -> None:
        """Llamada del asistente a ``n_calls`` tools y sus respuestas."""
        call_ids = [f"call_{uuid4().hex[:8]}" for _ in range(n_calls)]
        self._add(
            ChatMessageRole.assistant,
            content="",
            tool_call={"calls": [{"id": cid, "name": "search_documents"} for cid in call_ids]},
            llm_call_id=await self._llm_call("ok"),
        )
        for cid in call_ids:
            self._add(
                ChatMessageRole.tool,
                tool_call={"id": cid, "name": "search_documents", "arguments": {}},
                tool_result={"ok": True},
            )

    async def answer(self, text: str, *, failed: bool = False) -> None:
        status = "error" if failed else "ok"
        self._add(ChatMessageRole.assistant, content=text, llm_call_id=await self._llm_call(status))


async def _thread(db: AsyncSession) -> _Thread:
    tenant = Tenant(name=f"Chat history {uuid4().hex[:6]}")
    user = User(email=f"history-{uuid4().hex[:8]}@test.local", name="History")
    db.add_all([tenant, user])
    await db.flush()
    await set_tenant_context(db, str(tenant.id))
    thread = ChatThread(tenant_id=tenant.id, user_id=user.id, title="Gasto en tickets")
    db.add(thread)
    await db.flush()
    return _Thread(db, tenant.id, thread.id)


def _assert_valid_order(messages: list[dict[str, Any]]) -> None:
    """Cada respuesta de tool sigue a su llamada; cada llamada, a un usuario o a tools."""
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    pending: set[str] = set()
    previous = "user"
    for message in messages[1:]:
        role = message["role"]
        if role == "tool":
            assert message["tool_call_id"] in pending, "respuesta de tool sin su llamada"
            pending.discard(message["tool_call_id"])
        elif role == "assistant" and message.get("tool_calls"):
            assert previous in {"user", "tool"}, "llamada de tool sin usuario delante"
            pending = {call["id"] for call in message["tool_calls"]}
        previous = role


async def test_history_never_starts_in_the_middle_of_a_tool_chain(
    chat_schema_ready: None, db_session: AsyncSession
) -> None:
    thread = await _thread(db_session)
    # Las dos preguntas de tu hilo: 4 + 2 + 2 búsquedas y 4 + 2 + 2.
    for question in ("¿gasto en tickets?", "¿y por comercio?"):
        await thread.user(question)
        for n_calls in (4, 2, 2):
            await thread.tool_round(n_calls)
        await thread.answer("respuesta")
    await thread.user("¿y en septiembre?")
    await db_session.flush()

    history = await chat_service._load_history(
        db_session, tenant_id=thread.tenant_id, thread_id=thread.thread_id
    )
    messages = chat_service._history_to_llm_messages(history, system_prompt="SYS")

    # Los últimos 20 mensajes empezaban en una respuesta de tool: ahora se descarta
    # el turno cortado y se empieza en la segunda pregunta.
    assert history[0].role == ChatMessageRole.user
    assert history[0].content == "¿y por comercio?"
    assert history[-1].content == "¿y en septiembre?"
    _assert_valid_order(messages)


async def test_failed_turns_are_not_sent_back_to_the_model(
    chat_schema_ready: None, db_session: AsyncSession
) -> None:
    thread = await _thread(db_session)
    await thread.user("¿gasto en tickets?")
    await thread.tool_round(2)
    await thread.answer("respuesta buena")
    await thread.user("pregunta que falló")
    await thread.tool_round(1)  # una iteración correcta antes del fallo
    await thread.answer("Ha ocurrido un error al procesar la consulta.", failed=True)
    await thread.user("pregunta nueva")
    await db_session.flush()

    history = await chat_service._load_history(
        db_session, tenant_id=thread.tenant_id, thread_id=thread.thread_id
    )
    contents = [message.content for message in history if message.content]

    assert "pregunta que falló" not in contents
    assert "Ha ocurrido un error al procesar la consulta." not in contents
    assert contents == ["¿gasto en tickets?", "respuesta buena", "pregunta nueva"]
    _assert_valid_order(chat_service._history_to_llm_messages(history, system_prompt="SYS"))
