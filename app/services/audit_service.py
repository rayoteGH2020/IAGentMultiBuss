"""Servicio de auditoría append-only (Paso 16 / AGENTS.md §7)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from app.models import AuditLog

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

ACTION_CHAT_MESSAGE_SENT = "chat.message_sent"
ACTION_CHAT_TOOL_EXECUTED = "chat.tool_executed"
ACTION_KNOWLEDGE_CHAT_SEARCH = "knowledge.chat_search"
ACTION_CALENDAR_INTEGRATION_LINKED = "calendar.integration_linked"
ACTION_CALENDAR_INTEGRATION_UNLINKED = "calendar.integration_unlinked"
ACTION_CALENDAR_VOICE_TRANSCRIBED = "calendar.voice_transcribed"
ACTION_CALENDAR_EVENT_CREATED_FROM_VOICE = "calendar.event_created_from_voice"

RESOURCE_CHAT_MESSAGE = "chat_message"
RESOURCE_CHAT_THREAD = "chat_thread"
RESOURCE_KNOWLEDGE = "knowledge"
RESOURCE_CALENDAR_INTEGRATION = "calendar_integration"
RESOURCE_VOICE_TRANSCRIPTION = "voice_transcription"
RESOURCE_CALENDAR_EVENT = "calendar_event"


@dataclass(frozen=True, slots=True)
class AuditRequestContext:
    """Metadatos HTTP opcionales para entradas de audit_log."""

    ip: str | None = None
    user_agent: str | None = None


async def log_action(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
    request_ctx: AuditRequestContext | None = None,
) -> AuditLog:
    """Inserta una fila en ``audit_log`` (sin commit; lo hace el caller vía get_db)."""
    req = request_ctx or AuditRequestContext()
    row = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip=req.ip,
        user_agent=_truncate_user_agent(req.user_agent),
        metadata_=metadata,
    )
    db.add(row)
    await db.flush()
    logger.debug(
        "audit.logged",
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        tenant_id=str(tenant_id),
    )
    return row


async def log_chat_user_message(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    thread_id: UUID,
    message_id: UUID,
    content_length: int,
    request_ctx: AuditRequestContext | None = None,
) -> AuditLog:
    """Audita envío de mensaje usuario en chat documental."""
    return await log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_CHAT_MESSAGE_SENT,
        resource_type=RESOURCE_CHAT_MESSAGE,
        resource_id=message_id,
        metadata={
            "thread_id": str(thread_id),
            "content_length": content_length,
        },
        request_ctx=request_ctx,
    )


async def log_chat_tool_executed(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID | None,
    thread_id: UUID,
    tool_name: str,
    ok: bool,
    cost_eur: Decimal | float | None = None,
    llm_call_id: UUID | None = None,
    error: str | None = None,
) -> AuditLog:
    """Audita ejecución de una tool del loop de chat."""
    meta: dict[str, Any] = {
        "thread_id": str(thread_id),
        "tool_name": tool_name,
        "ok": ok,
    }
    if cost_eur is not None:
        meta["cost_eur"] = float(cost_eur)
    if llm_call_id is not None:
        meta["llm_call_id"] = str(llm_call_id)
    if error:
        meta["error"] = error[:500]
    return await log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_CHAT_TOOL_EXECUTED,
        resource_type=RESOURCE_CHAT_THREAD,
        resource_id=thread_id,
        metadata=meta,
    )


async def log_knowledge_chat_search(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID | None,
    thread_id: UUID,
    query_hash: str | None,
    citations_count: int,
) -> AuditLog:
    """Audita un turno de chat que usó búsqueda en base de conocimiento."""
    meta: dict[str, Any] = {
        "thread_id": str(thread_id),
        "citations_count": citations_count,
    }
    if query_hash:
        meta["query_hash"] = query_hash
    return await log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_KNOWLEDGE_CHAT_SEARCH,
        resource_type=RESOURCE_KNOWLEDGE,
        resource_id=thread_id,
        metadata=meta,
    )


async def log_calendar_integration_linked(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    integration_id: UUID,
    google_email: str | None,
    provider: str = "google",
    request_ctx: AuditRequestContext | None = None,
) -> AuditLog:
    """Audita vinculación de calendario externo (OAuth completado)."""
    meta: dict[str, Any] = {"provider": provider}
    if google_email:
        meta["google_email"] = google_email
    return await log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_CALENDAR_INTEGRATION_LINKED,
        resource_type=RESOURCE_CALENDAR_INTEGRATION,
        resource_id=integration_id,
        metadata=meta,
        request_ctx=request_ctx,
    )


async def log_calendar_integration_unlinked(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    integration_id: UUID,
    google_email: str | None,
    provider: str = "google",
    request_ctx: AuditRequestContext | None = None,
) -> AuditLog:
    """Audita desvinculación de calendario externo."""
    meta: dict[str, Any] = {"provider": provider}
    if google_email:
        meta["google_email"] = google_email
    return await log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_CALENDAR_INTEGRATION_UNLINKED,
        resource_type=RESOURCE_CALENDAR_INTEGRATION,
        resource_id=integration_id,
        metadata=meta,
        request_ctx=request_ctx,
    )


def _truncate_user_agent(value: str | None, *, max_len: int = 512) -> str | None:
    if value is None:
        return None
    return value[:max_len] if len(value) > max_len else value
