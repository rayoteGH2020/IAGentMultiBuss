"""Servicio de chat para canales externos (WhatsApp, Telegram) — Paso 21 E."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select, text

from app.config import get_settings
from app.llm.client import get_llm_client
from app.llm.prompts_loader import render_prompt
from app.llm.tools import build_channel_registry
from app.llm.tools.registry import ToolContext
from app.models.channel_response_cache import ChannelResponseCache
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


@dataclass(frozen=True, slots=True)
class _CacheHit:
    cache_id: str  # UUID as string for UPDATE
    answer_text: str
    confidence: float


async def _lookup_cache(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    query_vector: list[float],
    ttl_hours: int,
    similarity_threshold: float,
) -> _CacheHit | None:
    """Busca en el caché semántico una respuesta con similitud coseno >= threshold."""
    vec_str = "[" + ",".join(str(v) for v in query_vector) + "]"
    sql = text("""
        SELECT id, answer_text, confidence
        FROM channel_response_cache
        WHERE tenant_id = CAST(:tenant_id AS uuid)
          AND created_at > now() - CAST(:ttl AS interval)
          AND 1 - (question_embedding <=> CAST(:vec AS vector(512))) >= :threshold
        ORDER BY question_embedding <=> CAST(:vec AS vector(512))
        LIMIT 1
    """)
    result = await db.execute(
        sql,
        {
            "tenant_id": str(tenant_id),
            "ttl": f"{ttl_hours} hours",
            "vec": vec_str,
            "threshold": similarity_threshold,
        },
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    return _CacheHit(
        cache_id=str(row["id"]),
        answer_text=row["answer_text"],
        confidence=float(row["confidence"]),
    )


async def _increment_hit_count(db: AsyncSession, *, cache_id: str) -> None:
    await db.execute(
        text(
            "UPDATE channel_response_cache"
            " SET hit_count = hit_count + 1"
            " WHERE id = CAST(:id AS uuid)"
        ),
        {"id": cache_id},
    )


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

    Flujo con caché semántico:
      1. Si el caché está activo: embebe la pregunta y busca una respuesta similar
         en channel_response_cache (coseno >= similarity_threshold, TTL=ttl_hours).
      2. Cache hit: devuelve la respuesta cacheada sin llamar al LLM.
      3. Cache miss: ejecuta el pipeline RAG completo. Si confidence >=
         channel_cache_min_confidence, guarda la respuesta para futuros hits.

    En ambos casos persiste la conversación y los mensajes en channel_messages.
    No hace commit; el caller (ARQ job) es responsable.

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
    llm = get_llm_client()

    # 1. Embed + cache lookup (solo si el caché está activo)
    query_vector: list[float] | None = None
    cache_hit: _CacheHit | None = None
    if settings.channel_cache_enabled:
        vecs = await llm.embed([message_text], tenant_id=tenant.id, db=db)
        query_vector = vecs[0]
        cache_hit = await _lookup_cache(
            db,
            tenant_id=tenant.id,
            query_vector=query_vector,
            ttl_hours=settings.channel_cache_ttl_hours,
            similarity_threshold=settings.channel_cache_similarity_threshold,
        )

    # 2. Conversación: siempre necesaria para persistir los mensajes
    conversation = await _get_or_create_conversation(
        db,
        tenant_id=tenant.id,
        channel=channel,
        customer_identifier=customer_identifier,
    )

    if cache_hit is not None:
        answer_text = cache_hit.answer_text
        confidence = cache_hit.confidence
        citations_count = 0
        await _increment_hit_count(db, cache_id=cache_hit.cache_id)
        logger.info(
            "channel.cache_hit",
            tenant_id=str(tenant.id),
            channel=channel,
            customer_identifier=customer_identifier,
            confidence=confidence,
            cache_id=cache_hit.cache_id,
        )
    else:
        # 3. Pipeline RAG completo
        history = await _load_history(db, tenant_id=tenant.id, conversation_id=conversation.id)
        system_prompt = render_prompt(_CHANNEL_PROMPT_VERSION, company_name=tenant.name)
        llm_messages = _build_llm_messages(
            history, system_prompt=system_prompt, user_text=message_text
        )

        registry = build_channel_registry()
        ctx = ToolContext(db=db, tenant_id=tenant.id)

        loop_result = await llm.run_tool_loop(
            messages=llm_messages,
            registry=registry,
            ctx=ctx,
            tenant_id=tenant.id,
            db=db,
            prompt_version=_CHANNEL_PROMPT_VERSION,
        )

        if loop_result.citations:
            max_rrf = max(c.score for c in loop_result.citations)
            confidence = normalize_rrf_score(max_rrf, rrf_k=settings.knowledge_rrf_k)
        else:
            confidence = 0.0

        answer_text = loop_result.final_text
        citations_count = len(loop_result.citations)

        # 4. Guardar en caché si la respuesta tiene calidad suficiente
        if (
            settings.channel_cache_enabled
            and query_vector is not None
            and confidence >= settings.channel_cache_min_confidence
        ):
            db.add(
                ChannelResponseCache(
                    tenant_id=tenant.id,
                    channel=channel,
                    question_text=message_text,
                    question_embedding=query_vector,
                    answer_text=answer_text,
                    confidence=confidence,
                )
            )

        logger.info(
            "channel.answer_generated",
            tenant_id=str(tenant.id),
            channel=channel,
            customer_identifier=customer_identifier,
            confidence=confidence,
            citations=citations_count,
            knowledge_tools=loop_result.knowledge_tools_used,
        )

    # 5. Persistir mensajes (siempre, tanto en hit como en miss)
    db.add(
        ChannelMessage(
            conversation_id=conversation.id,
            tenant_id=tenant.id,
            role="user",
            content=message_text,
        )
    )
    db.add(
        ChannelMessage(
            conversation_id=conversation.id,
            tenant_id=tenant.id,
            role="assistant",
            content=answer_text,
            msg_metadata={
                "confidence": confidence,
                "citations_count": citations_count,
                "cache_hit": cache_hit is not None,
            },
        )
    )
    await db.flush()
    await usage_meter_service.increment_rag_messages_count(db, tenant_id=tenant.id)

    return ChannelResponse(
        text=answer_text,
        confidence=confidence,
        citations_count=citations_count,
    )
