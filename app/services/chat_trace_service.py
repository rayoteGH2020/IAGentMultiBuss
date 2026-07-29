"""Traza completa de hilos de /chat para la consola SuperAdmin.

Lecturas cross-tenant vía ``enable_superadmin_lookup`` (política RLS
``superadmin_select`` en chat_threads, chat_messages, audit_log y llm_calls).
Expone contenido de mensajes a propósito: solo SuperAdmin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select

from app.core.errors import NotFoundError
from app.models import AuditLog, ChatMessage, ChatThread, LLMCall, Tenant, User
from app.schemas.chat_trace import (
    ChatTraceAuditRead,
    ChatTraceMessageRead,
    ChatTraceThreadDetail,
    ChatTraceThreadListItem,
    LLMCallTraceRead,
)
from app.services.document_override_service import enable_superadmin_lookup

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_CHAT_AUDIT_ACTIONS = (
    "chat.message_sent",
    "chat.tool_executed",
    "knowledge.chat_search",
)


async def list_threads(
    db: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    tenant_id: UUID | None = None,
    include_hidden: bool = True,
) -> list[ChatTraceThreadListItem]:
    """Lista hilos de todos los tenants, más recientes primero."""
    await enable_superadmin_lookup(db)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    msg_count = (
        select(func.count(ChatMessage.id))
        .where(ChatMessage.thread_id == ChatThread.id)
        .correlate(ChatThread)
        .scalar_subquery()
        .label("message_count")
    )
    stmt = (
        select(ChatThread, Tenant.name, User.email, msg_count)
        .join(Tenant, Tenant.id == ChatThread.tenant_id)
        .outerjoin(User, User.id == ChatThread.user_id)
        .order_by(ChatThread.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if tenant_id is not None:
        stmt = stmt.where(ChatThread.tenant_id == tenant_id)
    if not include_hidden:
        stmt = stmt.where(ChatThread.is_hidden.is_(False))

    rows = (await db.execute(stmt)).all()
    return [
        ChatTraceThreadListItem(
            id=thread.id,
            tenant_id=thread.tenant_id,
            tenant_name=tenant_name,
            user_id=thread.user_id,
            user_email=user_email,
            title=thread.title,
            is_hidden=thread.is_hidden,
            message_count=int(message_count or 0),
            created_at=thread.created_at,
            updated_at=thread.updated_at,
        )
        for thread, tenant_name, user_email, message_count in rows
    ]


async def get_thread_trace(db: AsyncSession, *, thread_id: UUID) -> ChatTraceThreadDetail:
    """Detalle: mensajes en orden de creación + llm_calls + audit del hilo."""
    await enable_superadmin_lookup(db)

    thread_row = (
        await db.execute(
            select(ChatThread, Tenant.name, User.email)
            .join(Tenant, Tenant.id == ChatThread.tenant_id)
            .outerjoin(User, User.id == ChatThread.user_id)
            .where(ChatThread.id == thread_id)
        )
    ).one_or_none()
    if thread_row is None:
        raise NotFoundError("Chat thread not found")

    thread, tenant_name, user_email = thread_row
    messages = list(
        (
            await db.execute(
                select(ChatMessage)
                .where(
                    ChatMessage.thread_id == thread_id,
                    ChatMessage.tenant_id == thread.tenant_id,
                )
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            )
        )
        .scalars()
        .all()
    )

    audit_rows = list(
        (
            await db.execute(
                select(AuditLog)
                .where(
                    AuditLog.tenant_id == thread.tenant_id,
                    AuditLog.action.in_(_CHAT_AUDIT_ACTIONS),
                    AuditLog.metadata_["thread_id"].as_string() == str(thread_id),
                )
                .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
            )
        )
        .scalars()
        .all()
    )

    llm_ids: set[UUID] = {m.llm_call_id for m in messages if m.llm_call_id is not None}
    for row in audit_rows:
        meta = row.metadata_ or {}
        raw_id = meta.get("llm_call_id")
        if isinstance(raw_id, str):
            try:
                llm_ids.add(UUID(raw_id))
            except ValueError:
                continue

    llm_by_id: dict[UUID, LLMCall] = {}
    if llm_ids:
        llm_rows = (
            (await db.execute(select(LLMCall).where(LLMCall.id.in_(llm_ids)))).scalars().all()
        )
        llm_by_id = {call.id: call for call in llm_rows}

    # También recoge llamadas task=chat del tenant en la ventana temporal del
    # hilo que no quedaron enlazadas a un mensaje (p. ej. fallo antes de flush).
    if messages:
        window_start = messages[0].created_at
        window_end = messages[-1].created_at
        extra_calls = (
            (
                await db.execute(
                    select(LLMCall)
                    .where(
                        LLMCall.tenant_id == thread.tenant_id,
                        LLMCall.task == "chat",
                        LLMCall.created_at >= window_start,
                        LLMCall.created_at <= window_end,
                    )
                    .order_by(LLMCall.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        for call in extra_calls:
            llm_by_id.setdefault(call.id, call)

    message_reads = [
        ChatTraceMessageRead(
            id=msg.id,
            thread_id=msg.thread_id,
            tenant_id=msg.tenant_id,
            role=msg.role,
            content=msg.content,
            tool_call=msg.tool_call,
            tool_result=msg.tool_result,
            citations=msg.citations,
            llm_call_id=msg.llm_call_id,
            created_at=msg.created_at,
            llm_call=(
                LLMCallTraceRead.model_validate(llm_by_id[msg.llm_call_id])
                if msg.llm_call_id and msg.llm_call_id in llm_by_id
                else None
            ),
        )
        for msg in messages
    ]

    llm_calls = [
        LLMCallTraceRead.model_validate(row)
        for row in sorted(llm_by_id.values(), key=lambda c: (c.created_at, str(c.id)))
    ]
    audit_events = [
        ChatTraceAuditRead(
            id=row.id,
            tenant_id=row.tenant_id,
            user_id=row.user_id,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            metadata=row.metadata_,
            created_at=row.created_at,
        )
        for row in audit_rows
    ]

    return ChatTraceThreadDetail(
        thread=ChatTraceThreadListItem(
            id=thread.id,
            tenant_id=thread.tenant_id,
            tenant_name=tenant_name,
            user_id=thread.user_id,
            user_email=user_email,
            title=thread.title,
            is_hidden=thread.is_hidden,
            message_count=len(messages),
            created_at=thread.created_at,
            updated_at=thread.updated_at,
        ),
        messages=message_reads,
        llm_calls=llm_calls,
        audit_events=audit_events,
    )
