"""Cuotas cuantitativas por plan: enforcement unico (Paso04)."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.core.entitlement_codes import (
    LIMIT_CHANNEL_EXTERNAL_SLOTS,
    LIMIT_CHANNEL_MESSAGES_PER_HOUR,
    LIMIT_CHAT_MESSAGES_PER_DAY,
    LIMIT_DOCUMENT_RETRIES_PER_DAY,
    LIMIT_DOCUMENTS_PER_DAY,
    LIMIT_KNOWLEDGE_DOCS_MAX,
    LIMIT_KNOWLEDGE_UPLOADS_PER_DAY,
    LIMIT_LLM_BUDGET_EUR_MONTH,
    LIMIT_MEMBERS_MAX,
    LIMIT_VOICE_NOTES_PER_HOUR,
)
from app.core.errors import RateLimitError, ValidationError
from app.core.plan_limits import (
    MSG_CHANNEL_SLOTS,
    MSG_KNOWLEDGE_DOCS_MAX,
    MSG_LLM_BUDGET_MONTH,
    MSG_MEMBERS_MAX,
    resolve_budget_cap,
    resolve_quota_cap,
)
from app.core.rate_limiter import (
    chat_messages_tenant_key,
    check_channel_messages_rate,
    check_chat_messages_rate,
    check_documents_upload_rate,
    check_knowledge_upload_rate,
    check_voice_notes_rate,
    document_retries_key,
    documents_upload_key,
    knowledge_upload_key,
)
from app.models.channel_integration import ChannelIntegration
from app.models.knowledge import KnowledgeDocument
from app.models.membership import Membership
from app.schemas.channel import ChannelIntegrationStatus
from app.schemas.entitlements import QuotaUsage
from app.schemas.knowledge import KnowledgeDocumentStatus
from app.services import usage_meter_service

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.schemas.entitlements import Entitlements


def _settings() -> Settings:
    return get_settings()


def _platform_cap(code: str, settings: Settings) -> int | None:
    """Tope global de plataforma (kill-switch) que puede rebajar el del plan."""
    caps: dict[str, int | None] = {
        LIMIT_KNOWLEDGE_UPLOADS_PER_DAY: settings.knowledge_max_uploads_per_day,
        LIMIT_CHAT_MESSAGES_PER_DAY: settings.chat_daily_message_limit,
        LIMIT_CHANNEL_MESSAGES_PER_HOUR: settings.channel_rate_limit_msg_per_hour,
        LIMIT_VOICE_NOTES_PER_HOUR: settings.voice_rate_limit_per_hour,
    }
    return caps.get(code)


def effective_quota_cap(ents: Entitlements, code: str) -> int | None:
    """Tope que realmente se aplica (plan + override + tope de plataforma).

    Única fuente para el enforcement y para lo que ve el cliente en Mi cuenta.
    """
    return resolve_quota_cap(ents, code, platform_cap=_platform_cap(code, _settings()))


async def ensure_documents_upload(
    redis: Any,
    ents: Entitlements,
    tenant_id: UUID,
    *,
    n_files: int = 1,
) -> None:
    cap = resolve_quota_cap(
        ents,
        LIMIT_DOCUMENTS_PER_DAY,
        platform_cap=None,
    )
    await check_documents_upload_rate(
        redis,
        tenant_id=tenant_id,
        max_per_day=cap,
        n_files=n_files,
    )


async def ensure_document_retry(
    redis: Any,
    ents: Entitlements,
    tenant_id: UUID,
) -> None:
    """Comprueba cupo de reintentos sin consumir (consumo tras encolar OK)."""
    cap = resolve_quota_cap(
        ents,
        LIMIT_DOCUMENT_RETRIES_PER_DAY,
        platform_cap=None,
    )
    from app.core.plan_limits import MSG_DOCUMENT_RETRIES_DAILY
    from app.core.rate_limiter import assert_quota_headroom, document_retries_key

    key = document_retries_key(tenant_id)

    await assert_quota_headroom(
        redis,
        key=key,
        delta=1,
        max_count=cap,
        error_message=MSG_DOCUMENT_RETRIES_DAILY,
        log_event="documents.retry.rate_limit",
        tenant_id=str(tenant_id),
    )


async def record_document_retry(
    redis: Any,
    tenant_id: UUID,
) -> None:
    """Registra un reintento consumido tras encolar con exito."""
    from app.core.rate_limiter import daily_ttl_seconds, document_retries_key, record_quota_usage

    await record_quota_usage(
        redis, key=document_retries_key(tenant_id), delta=1, ttl_seconds=daily_ttl_seconds()
    )


async def ensure_knowledge_upload(
    redis: Any,
    ents: Entitlements,
    tenant_id: UUID,
    *,
    n_files: int = 1,
) -> None:
    cap = effective_quota_cap(ents, LIMIT_KNOWLEDGE_UPLOADS_PER_DAY)
    await check_knowledge_upload_rate(
        redis,
        tenant_id=tenant_id,
        max_per_day=cap,
        n_files=n_files,
    )


async def ensure_chat_message(
    redis: Any,
    ents: Entitlements,
    tenant_id: UUID,
    user_id: UUID,
) -> None:
    cap = effective_quota_cap(ents, LIMIT_CHAT_MESSAGES_PER_DAY)
    user_cap = _settings().chat_user_daily_message_limit
    await check_chat_messages_rate(
        redis,
        tenant_id=tenant_id,
        user_id=user_id,
        max_per_day=cap,
        max_per_user_day=user_cap if user_cap > 0 else None,
    )


async def ensure_channel_message_allowed(
    redis: Any,
    ents: Entitlements,
    tenant_id: UUID,
    customer_identifier: str,
) -> bool:
    cap = effective_quota_cap(ents, LIMIT_CHANNEL_MESSAGES_PER_HOUR)
    return await check_channel_messages_rate(
        redis,
        tenant_id=tenant_id,
        customer_identifier=customer_identifier,
        max_per_hour=cap,
    )


async def ensure_voice_note(
    redis: Any,
    ents: Entitlements,
    tenant_id: UUID,
    user_id: UUID,
) -> None:
    cap = effective_quota_cap(ents, LIMIT_VOICE_NOTES_PER_HOUR)
    await check_voice_notes_rate(
        redis,
        tenant_id=tenant_id,
        user_id=user_id,
        max_per_hour=cap,
    )


async def _count_active_members(db: AsyncSession, tenant_id: UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.tenant_id == tenant_id,
            Membership.is_active.is_(True),
        )
    )
    return int((await db.execute(stmt)).scalar_one())


async def ensure_member_capacity(
    db: AsyncSession,
    ents: Entitlements,
    tenant_id: UUID,
    *,
    adding: int = 1,
) -> None:
    cap = resolve_quota_cap(ents, LIMIT_MEMBERS_MAX, platform_cap=None)
    if cap is None:
        return
    if cap <= 0:
        raise ValidationError(MSG_MEMBERS_MAX)
    current = await _count_active_members(db, tenant_id)
    if current + adding > cap:
        raise ValidationError(MSG_MEMBERS_MAX)


async def _count_knowledge_docs(db: AsyncSession, tenant_id: UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(KnowledgeDocument)
        .where(
            KnowledgeDocument.tenant_id == tenant_id,
            KnowledgeDocument.status != KnowledgeDocumentStatus.failed,
        )
    )
    return int((await db.execute(stmt)).scalar_one())


async def ensure_knowledge_docs_capacity(
    db: AsyncSession,
    ents: Entitlements,
    tenant_id: UUID,
    *,
    adding: int = 1,
) -> None:
    cap = resolve_quota_cap(ents, LIMIT_KNOWLEDGE_DOCS_MAX, platform_cap=None)
    if cap is None:
        return
    if cap <= 0:
        raise ValidationError(MSG_KNOWLEDGE_DOCS_MAX)
    current = await _count_knowledge_docs(db, tenant_id)
    if current + adding > cap:
        raise ValidationError(MSG_KNOWLEDGE_DOCS_MAX)


async def _count_active_channels(db: AsyncSession, tenant_id: UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(ChannelIntegration)
        .where(
            ChannelIntegration.tenant_id == tenant_id,
            ChannelIntegration.status == ChannelIntegrationStatus.active.value,
        )
    )
    return int((await db.execute(stmt)).scalar_one())


async def ensure_channel_slot(
    db: AsyncSession,
    ents: Entitlements,
    tenant_id: UUID,
) -> None:
    """Rechaza conectar un canal nuevo si el plan no tiene plazas libres.

    Solo para altas: actualizar o reconectar un canal ya activo no ocupa plaza.
    """
    cap = effective_quota_cap(ents, LIMIT_CHANNEL_EXTERNAL_SLOTS)
    if cap is None:
        return
    if cap <= 0 or await _count_active_channels(db, tenant_id) >= cap:
        raise ValidationError(MSG_CHANNEL_SLOTS, details={"code": "channel_slots_max"})


async def _redis_count(redis: Any, key: str) -> int | None:
    """Valor de un contador diario; None si Redis no responde (la página no cae)."""
    try:
        raw = await redis.get(key)
    except Exception:
        logger.warning("quota.usage_redis_unavailable", key_prefix=key.rsplit(":", 2)[0])
        return None
    return int(raw) if raw is not None else 0


async def get_limit_usage(
    db: AsyncSession,
    redis: Any,
    ents: Entitlements,
    tenant_id: UUID,
) -> dict[str, QuotaUsage]:
    """Consumo actual de cada límite medible, con el tope que realmente se aplica.

    Lee las mismas claves Redis (día local) y recuentos que el enforcement.
    Fuera: mensajes por cliente y hora en canales (no hay total del tenant) y
    los límites cuyo consumo no se pudo leer.
    """
    counts: dict[str, int | None] = {
        LIMIT_DOCUMENTS_PER_DAY: await _redis_count(redis, documents_upload_key(tenant_id)),
        LIMIT_DOCUMENT_RETRIES_PER_DAY: await _redis_count(redis, document_retries_key(tenant_id)),
        LIMIT_KNOWLEDGE_UPLOADS_PER_DAY: await _redis_count(redis, knowledge_upload_key(tenant_id)),
        LIMIT_CHAT_MESSAGES_PER_DAY: await _redis_count(redis, chat_messages_tenant_key(tenant_id)),
        LIMIT_KNOWLEDGE_DOCS_MAX: await _count_knowledge_docs(db, tenant_id),
        LIMIT_MEMBERS_MAX: await _count_active_members(db, tenant_id),
        LIMIT_CHANNEL_EXTERNAL_SLOTS: await _count_active_channels(db, tenant_id),
    }
    return {
        code: QuotaUsage(used=used, cap=effective_quota_cap(ents, code))
        for code, used in counts.items()
        if used is not None
    }


async def ensure_llm_budget(
    db: AsyncSession,
    ents: Entitlements,
    tenant_id: UUID,
) -> None:
    """Bloquea llamadas LLM caras si el tenant supero el presupuesto mensual."""
    budget = resolve_budget_cap(ents, LIMIT_LLM_BUDGET_EUR_MONTH, platform_cap_eur=None)
    if budget is None:
        return
    if budget <= 0:
        raise RateLimitError(MSG_LLM_BUDGET_MONTH)
    spent = await usage_meter_service.get_llm_cost_eur(db, tenant_id=tenant_id)
    if spent >= budget:
        raise RateLimitError(MSG_LLM_BUDGET_MONTH)


async def record_llm_cost(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    cost_eur: Decimal,
) -> None:
    """Suma coste real a ``usage_meter`` tras una llamada LLM exitosa."""
    if cost_eur <= 0:
        return
    await usage_meter_service.add_llm_cost_eur(db, tenant_id=tenant_id, delta=cost_eur)


async def get_usage_snapshot(
    db: AsyncSession,
    ents: Entitlements,
    tenant_id: UUID,
) -> dict[str, object]:
    """Resumen de uso mensual para Settings / SADM (solo metadatos agregados)."""
    period = usage_meter_service.current_billing_period()
    meter = await usage_meter_service.get_meter(db, tenant_id=tenant_id, period=period)
    llm_spent = meter.llm_cost_eur if meter is not None else Decimal("0")
    budget = resolve_budget_cap(ents, LIMIT_LLM_BUDGET_EUR_MONTH, platform_cap_eur=None)
    return {
        "period": period,
        "llm_cost_eur": llm_spent,
        "llm_budget_eur": budget,
        "rag_messages_count": meter.rag_messages_count if meter is not None else 0,
        "invoices_count": meter.invoices_count if meter is not None else 0,
        "plan_code": ents.plan_code,
    }
