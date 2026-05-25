"""Orquestación del chat de consulta documental (módulo 1.5)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import func, select

from app.config import get_settings
from app.core.errors import ForbiddenError, NotFoundError, RateLimitError, ValidationError
from app.llm.client import get_llm_client
from app.llm.prompts_loader import load_prompt
from app.llm.tools.document_chat import PROMPT_VERSION
from app.llm.tools.registry import ToolContext
from app.models import ChatMessage, ChatMessageRole, ChatThread
from app.schemas.chat import (
    ChatMessageListFilters,
    ChatMessageRead,
    ChatThreadListFilters,
    ChatThreadRead,
)
from app.schemas.pagination import Page
from app.services import audit_service, chat_tool_runner
from app.services.audit_service import AuditRequestContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    import redis.asyncio as redis
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.llm.chat_loop import TurnMessageRecord

logger = structlog.get_logger(__name__)

_RATE_KEY_PREFIX = "chat:rate"
_RATE_TTL_SECONDS = 86400


async def enforce_rate_limit(
    redis_conn: redis.Redis,
    *,
    tenant_id: UUID,
    user_id: UUID,
) -> None:
    """Token bucket diario por usuario y tenant en Redis."""
    settings = get_settings()
    key = f"{_RATE_KEY_PREFIX}:{tenant_id}:{user_id}:{date.today().isoformat()}"
    count = int(await redis_conn.incr(key))
    if count == 1:
        await redis_conn.expire(key, _RATE_TTL_SECONDS)
    if count > settings.chat_daily_message_limit:
        raise RateLimitError(
            "Has alcanzado el límite diario de mensajes de chat. Inténtalo mañana.",
            details={"limit": settings.chat_daily_message_limit},
        )


def validate_message_content(content: str) -> str:
    """Normaliza y valida longitud del mensaje usuario."""
    text = content.strip()
    if not text:
        raise ValidationError("El mensaje no puede estar vacío")
    max_bytes = get_settings().chat_max_message_bytes
    if len(text.encode("utf-8")) > max_bytes:
        raise ValidationError(
            f"El mensaje supera el límite de {max_bytes} bytes",
            details={"max_bytes": max_bytes},
        )
    return text


async def create_thread(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    title: str | None = None,
) -> ChatThreadRead:
    """Crea un hilo vacío para el usuario."""
    thread = ChatThread(
        tenant_id=tenant_id,
        user_id=user_id,
        title=title[:200] if title else None,
    )
    db.add(thread)
    await db.flush()
    return ChatThreadRead.model_validate(thread)


async def list_threads(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    filters: ChatThreadListFilters | None = None,
) -> Page[ChatThreadRead]:
    """Lista hilos del usuario ordenados por actividad reciente."""
    f = filters or ChatThreadListFilters()
    base = select(ChatThread).where(
        ChatThread.tenant_id == tenant_id,
        ChatThread.user_id == user_id,
    )
    count_stmt = select(func.count()).select_from(base.subquery())
    total = int((await db.execute(count_stmt)).scalar_one())

    stmt = base.order_by(ChatThread.updated_at.desc()).limit(f.limit).offset(f.offset)
    result = await db.execute(stmt)
    threads = result.scalars().all()
    return Page(
        items=[ChatThreadRead.model_validate(t) for t in threads],
        total=total,
        limit=f.limit,
        offset=f.offset,
    )


async def get_thread(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    thread_id: UUID,
) -> ChatThread:
    """Carga un hilo verificando tenant y ownership."""
    stmt = select(ChatThread).where(
        ChatThread.tenant_id == tenant_id,
        ChatThread.id == thread_id,
    )
    result = await db.execute(stmt)
    thread = result.scalar_one_or_none()
    if thread is None:
        raise NotFoundError(f"Chat thread {thread_id} not found")
    if thread.user_id != user_id:
        raise ForbiddenError("You do not have access to this chat thread")
    return thread


async def list_messages(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    thread_id: UUID,
    filters: ChatMessageListFilters | None = None,
) -> list[ChatMessageRead]:
    """Mensajes del hilo (más recientes primero, acotado por filtros)."""
    await get_thread(db, tenant_id=tenant_id, user_id=user_id, thread_id=thread_id)
    f = filters or ChatMessageListFilters()
    stmt = (
        select(ChatMessage)
        .where(
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.thread_id == thread_id,
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(f.limit)
        .offset(f.offset)
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    rows.reverse()
    return [ChatMessageRead.model_validate(m) for m in rows]


def _history_to_llm_messages(
    history: list[ChatMessage],
    *,
    system_prompt: str,
) -> list[dict[str, Any]]:
    """Convierte historial BD a mensajes para el loop LLM."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for msg in history:
        if msg.role == ChatMessageRole.user:
            messages.append({"role": "user", "content": msg.content or ""})
        elif msg.role == ChatMessageRole.assistant:
            entry: dict[str, Any] = {
                "role": "assistant",
                "content": msg.content or "",
            }
            if msg.tool_call:
                entry["tool_calls"] = msg.tool_call.get("calls", [])
            messages.append(entry)
        elif msg.role == ChatMessageRole.tool and msg.tool_call:
            tool_content = msg.tool_result if msg.tool_result is not None else {}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(msg.tool_call.get("id", "")),
                    "name": str(msg.tool_call.get("name", "")),
                    "content": tool_content,
                },
            )
    return messages


async def _load_history(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    thread_id: UUID,
) -> list[ChatMessage]:
    limit = get_settings().chat_history_message_limit
    stmt = (
        select(ChatMessage)
        .where(
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.thread_id == thread_id,
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    rows.reverse()
    return rows


async def _persist_turn_messages(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    thread_id: UUID,
    records: tuple[TurnMessageRecord, ...],
) -> ChatMessage | None:
    """Persiste mensajes assistant/tool del turno; devuelve el assistant final."""
    last_assistant: ChatMessage | None = None
    for record in records:
        role = ChatMessageRole(record.role)
        message = ChatMessage(
            tenant_id=tenant_id,
            thread_id=thread_id,
            role=role,
            content=record.content,
            tool_call=record.tool_call,
            tool_result=record.tool_result,
            llm_call_id=record.llm_call_id,
        )
        db.add(message)
        if role == ChatMessageRole.assistant:
            last_assistant = message
    await db.flush()
    return last_assistant


def _chunk_text(text: str, *, chunk_size: int) -> list[str]:
    if not text:
        return [""]
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


async def _run_assistant_turn(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    thread_id: UUID,
) -> AsyncIterator[str]:
    """Ejecuta el loop LLM y persiste assistant/tool; asume historial ya en BD."""
    thread = await get_thread(db, tenant_id=tenant_id, user_id=user_id, thread_id=thread_id)
    history = await _load_history(db, tenant_id=tenant_id, thread_id=thread_id)
    system_prompt = load_prompt(PROMPT_VERSION)
    llm_messages = _history_to_llm_messages(history, system_prompt=system_prompt)

    registry = chat_tool_runner.get_chat_registry()
    ctx = ToolContext(db=db, tenant_id=tenant_id, user_id=user_id, thread_id=thread_id)
    loop_result = await get_llm_client().run_tool_loop(
        messages=llm_messages,
        registry=registry,
        ctx=ctx,
        tenant_id=tenant_id,
        db=db,
        prompt_version=PROMPT_VERSION,
    )

    await _persist_turn_messages(
        db,
        tenant_id=tenant_id,
        thread_id=thread_id,
        records=loop_result.turn_messages,
    )
    thread.updated_at = datetime.now(tz=UTC)
    await db.flush()

    logger.info(
        "chat.turn_completed",
        thread_id=str(thread_id),
        tenant_id=str(tenant_id),
        tools=loop_result.tool_calls_executed,
        llm_calls=len(loop_result.llm_call_ids),
    )

    chunk_size = get_settings().chat_stream_chunk_chars
    for chunk in _chunk_text(loop_result.final_text, chunk_size=chunk_size):
        yield chunk


async def post_user_message(
    db: AsyncSession,
    redis_conn: redis.Redis,
    *,
    tenant_id: UUID,
    user_id: UUID,
    thread_id: UUID,
    content: str,
    request_ctx: AuditRequestContext | None = None,
) -> ChatMessageRead:
    """Persiste mensaje usuario (rate-limit + ownership); sin ejecutar el LLM."""
    text = validate_message_content(content)
    await enforce_rate_limit(redis_conn, tenant_id=tenant_id, user_id=user_id)

    thread = await get_thread(db, tenant_id=tenant_id, user_id=user_id, thread_id=thread_id)

    user_message = ChatMessage(
        tenant_id=tenant_id,
        thread_id=thread_id,
        role=ChatMessageRole.user,
        content=text,
    )
    db.add(user_message)
    if not thread.title:
        thread.title = text[:200]
    thread.updated_at = datetime.now(tz=UTC)
    await db.flush()
    await audit_service.log_chat_user_message(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        thread_id=thread_id,
        message_id=user_message.id,
        content_length=len(text.encode("utf-8")),
        request_ctx=request_ctx,
    )
    return ChatMessageRead.model_validate(user_message)


async def send_message(
    db: AsyncSession,
    redis_conn: redis.Redis,
    *,
    tenant_id: UUID,
    user_id: UUID,
    thread_id: UUID,
    content: str,
) -> AsyncIterator[str]:
    """Persiste mensaje user, ejecuta tool loop, persiste assistant; yield chunks SSE."""
    await post_user_message(
        db,
        redis_conn,
        tenant_id=tenant_id,
        user_id=user_id,
        thread_id=thread_id,
        content=content,
    )

    async for chunk in _run_assistant_turn(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        thread_id=thread_id,
    ):
        yield chunk


async def stream_reply(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    thread_id: UUID,
    user_message_id: UUID,
) -> AsyncIterator[str]:
    """SSE sobre un mensaje user ya persistido (sin duplicar el mensaje en BD)."""
    await get_thread(db, tenant_id=tenant_id, user_id=user_id, thread_id=thread_id)
    stmt = select(ChatMessage).where(
        ChatMessage.tenant_id == tenant_id,
        ChatMessage.thread_id == thread_id,
        ChatMessage.id == user_message_id,
        ChatMessage.role == ChatMessageRole.user,
    )
    result = await db.execute(stmt)
    user_message = result.scalar_one_or_none()
    if user_message is None:
        raise NotFoundError(f"User message {user_message_id} not found")

    later_stmt = (
        select(func.count())
        .select_from(ChatMessage)
        .where(
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.thread_id == thread_id,
            ChatMessage.created_at > user_message.created_at,
        )
    )
    later_count = int((await db.execute(later_stmt)).scalar_one())
    if later_count > 0:
        raise ValidationError("This message was already processed")

    async for chunk in _run_assistant_turn(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        thread_id=thread_id,
    ):
        yield chunk
