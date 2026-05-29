"""Servicio de chat para canales externos (WhatsApp, Telegram) — Paso 21 E."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.llm.client import get_llm_client
from app.llm.prompts_loader import render_prompt
from app.llm.tools import build_channel_registry
from app.llm.tools.registry import ToolContext
from app.models.conversation import ChannelMessage, Conversation
from app.models.tenant import Tenant
from app.schemas.channel import ChannelResponse
from app.services import usage_meter_service
from app.services.knowledge_search_service import normalize_rrf_score

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

_CHANNEL_PROMPT_VERSION = "channel_external_v1"
_HISTORY_LIMIT = 10


async def _get_or_create_conversation(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    channel: str,
    customer_identifier: str,
) -> Conversation:
    """Retorna la conversación abierta más reciente o crea una nueva."""
    stmt = (
        select(Conversation)
        .where(
            Conversation.tenant_id == tenant_id,
            Conversation.channel == channel,
            Conversation.customer_identifier == customer_identifier,
            Conversation.closed_at.is_(None),
        )
        .order_by(Conversation.started_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    conv = result.scalar_one_or_none()
    if conv is None:
        conv = Conversation(
            tenant_id=tenant_id,
            channel=channel,
            customer_identifier=customer_identifier,
        )
        db.add(conv)
        await db.flush()
    return conv


async def _load_history(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    conversation_id: UUID,
) -> list[ChannelMessage]:
    stmt = (
        select(ChannelMessage)
        .where(
            ChannelMessage.tenant_id == tenant_id,
            ChannelMessage.conversation_id == conversation_id,
        )
        .order_by(ChannelMessage.created_at.desc())
        .limit(_HISTORY_LIMIT)
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    rows.reverse()
    return rows


def _build_llm_messages(
    history: list[ChannelMessage],
    *,
    system_prompt: str,
    user_text: str,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": user_text})
    return messages


async def answer_for_channel(
    db: AsyncSession,
    tenant: Tenant,
    *,
    channel: str,
    customer_identifier: str,
    message_text: str,
) -> ChannelResponse:
    """Genera una respuesta RAG para un mensaje de cliente externo.

    Usa solo tools de conocimiento (build_channel_registry) — nunca tools
    documentales (facturas, tickets). Persiste la conversación y los mensajes
    en channel_messages. No hace commit; el caller (ARQ job) es responsable.

    Args:
        db: Sesión con contexto RLS del tenant activo.
        tenant: Tenant asociado al canal de entrada.
        channel: 'whatsapp' | 'telegram'.
        customer_identifier: Teléfono E.164 (WA) o chat_id (Telegram).
        message_text: Texto del mensaje del cliente.

    Returns:
        ChannelResponse con texto, confianza (0-1) y número de citas.
    """
    settings = get_settings()
    system_prompt = render_prompt(_CHANNEL_PROMPT_VERSION, company_name=tenant.name)

    conversation = await _get_or_create_conversation(
        db,
        tenant_id=tenant.id,
        channel=channel,
        customer_identifier=customer_identifier,
    )
    history = await _load_history(db, tenant_id=tenant.id, conversation_id=conversation.id)
    llm_messages = _build_llm_messages(history, system_prompt=system_prompt, user_text=message_text)

    registry = build_channel_registry()
    ctx = ToolContext(db=db, tenant_id=tenant.id)

    loop_result = await get_llm_client().run_tool_loop(
        messages=llm_messages,
        registry=registry,
        ctx=ctx,
        tenant_id=tenant.id,
        db=db,
        prompt_version=_CHANNEL_PROMPT_VERSION,
    )

    # Confidence from maximum RRF score among citations (normalized to 0-1)
    if loop_result.citations:
        max_rrf = max(c.score for c in loop_result.citations)
        confidence = normalize_rrf_score(max_rrf, rrf_k=settings.knowledge_rrf_k)
    else:
        confidence = 0.0

    # Persist user message
    db.add(
        ChannelMessage(
            conversation_id=conversation.id,
            tenant_id=tenant.id,
            role="user",
            content=message_text,
        )
    )

    # Persist assistant message with metadata
    db.add(
        ChannelMessage(
            conversation_id=conversation.id,
            tenant_id=tenant.id,
            role="assistant",
            content=loop_result.final_text,
            msg_metadata={
                "confidence": confidence,
                "citations_count": len(loop_result.citations),
                "tools_used": loop_result.tool_calls_executed,
            },
        )
    )
    await db.flush()
    await usage_meter_service.increment_rag_messages_count(db, tenant_id=tenant.id)

    logger.info(
        "channel.answer_generated",
        tenant_id=str(tenant.id),
        channel=channel,
        customer_identifier=customer_identifier,
        confidence=confidence,
        citations=len(loop_result.citations),
        knowledge_tools=loop_result.knowledge_tools_used,
    )

    return ChannelResponse(
        text=loop_result.final_text,
        confidence=confidence,
        citations_count=len(loop_result.citations),
    )
