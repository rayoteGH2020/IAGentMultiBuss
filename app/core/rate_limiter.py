"""Rate limiter Redis para cuotas por tenant (Paso04 / Paso18).

Patron INCRBY + TTL con incremento especulativo y rollback si se supera el tope.
Ventanas cortas (dia/hora); agregados mensuales en ``usage_meter``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from app.core.errors import RateLimitError

logger = structlog.get_logger(__name__)

_DAY_SECONDS: int = 86400
_HOUR_SECONDS: int = 3600


async def increment_quota(
    redis: Any,
    *,
    key: str,
    delta: int,
    max_count: int | None,
    ttl_seconds: int,
    error_message: str,
    log_event: str,
    **log_extra: object,
) -> None:
    """Incrementa contador Redis; revierte y lanza si supera ``max_count``.

    ``max_count`` None = ilimitado (no-op).
    """
    if max_count is None:
        return
    if max_count <= 0:
        raise RateLimitError(error_message)
    if delta <= 0:
        return

    new_count = int(await redis.incrby(key, delta))
    if new_count == delta:
        await redis.expire(key, ttl_seconds)

    if new_count > max_count:
        await redis.decrby(key, delta)
        logger.warning(log_event, max_count=max_count, new_count=new_count, **log_extra)
        raise RateLimitError(error_message)


async def assert_quota_headroom(
    redis: Any,
    *,
    key: str,
    delta: int,
    max_count: int | None,
    error_message: str,
    log_event: str,
    **log_extra: object,
) -> None:
    """Comprueba cupo sin incrementar (p. ej. antes de encolar un reintento)."""
    if max_count is None:
        return
    if max_count <= 0:
        raise RateLimitError(error_message)
    raw = await redis.get(key)
    current = int(raw) if raw is not None else 0
    if current + delta > max_count:
        logger.warning(
            log_event,
            max_count=max_count,
            current=current,
            delta=delta,
            **log_extra,
        )
        raise RateLimitError(error_message)


async def record_quota_usage(
    redis: Any,
    *,
    key: str,
    delta: int,
    ttl_seconds: int,
) -> None:
    """Incrementa contador tras operacion exitosa (sin comprobar tope)."""
    if delta <= 0:
        return
    new_count = int(await redis.incrby(key, delta))
    if new_count == delta:
        await redis.expire(key, ttl_seconds)


def _utc_date_key() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _utc_hour_key() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H")


async def check_documents_upload_rate(
    redis: Any,
    *,
    tenant_id: UUID,
    max_per_day: int | None,
    n_files: int = 1,
) -> None:
    key = f"rate:documents_upload:{tenant_id}:{_utc_date_key()}"
    from app.core.plan_limits import MSG_DOCUMENTS_DAILY

    await increment_quota(
        redis,
        key=key,
        delta=n_files,
        max_count=max_per_day,
        ttl_seconds=_DAY_SECONDS,
        error_message=MSG_DOCUMENTS_DAILY,
        log_event="documents.upload.rate_limit",
        tenant_id=str(tenant_id),
        n_files=n_files,
    )


async def check_document_retries_rate(
    redis: Any,
    *,
    tenant_id: UUID,
    max_per_day: int | None,
) -> None:
    key = f"rate:document_retries:{tenant_id}:{_utc_date_key()}"
    from app.core.plan_limits import MSG_DOCUMENT_RETRIES_DAILY

    await increment_quota(
        redis,
        key=key,
        delta=1,
        max_count=max_per_day,
        ttl_seconds=_DAY_SECONDS,
        error_message=MSG_DOCUMENT_RETRIES_DAILY,
        log_event="documents.retry.rate_limit",
        tenant_id=str(tenant_id),
    )


async def check_knowledge_upload_rate(
    redis: Any,
    *,
    tenant_id: UUID,
    max_per_day: int | None,
    n_files: int = 1,
) -> None:
    key = f"rate:knowledge_upload:{tenant_id}:{_utc_date_key()}"
    from app.core.plan_limits import MSG_KNOWLEDGE_UPLOADS_DAILY

    await increment_quota(
        redis,
        key=key,
        delta=n_files,
        max_count=max_per_day,
        ttl_seconds=_DAY_SECONDS,
        error_message=MSG_KNOWLEDGE_UPLOADS_DAILY,
        log_event="knowledge.upload.rate_limit",
        tenant_id=str(tenant_id),
        n_files=n_files,
    )


async def check_chat_messages_rate(
    redis: Any,
    *,
    tenant_id: UUID,
    user_id: UUID,
    max_per_day: int | None,
    max_per_user_day: int | None = None,
) -> None:
    """Cuota diaria de chat: primero por usuario, luego por tenant (pool del plan)."""
    from app.core.plan_limits import MSG_CHAT_MESSAGES_DAILY, MSG_CHAT_MESSAGES_USER_DAILY

    date_key = _utc_date_key()
    user_key = f"rate:chat_messages:{tenant_id}:{user_id}:{date_key}"
    tenant_key = f"rate:chat_messages:{tenant_id}:{date_key}"

    await increment_quota(
        redis,
        key=user_key,
        delta=1,
        max_count=max_per_user_day,
        ttl_seconds=_DAY_SECONDS,
        error_message=MSG_CHAT_MESSAGES_USER_DAILY,
        log_event="chat.messages.user_rate_limit",
        tenant_id=str(tenant_id),
        user_id=str(user_id),
    )
    try:
        await increment_quota(
            redis,
            key=tenant_key,
            delta=1,
            max_count=max_per_day,
            ttl_seconds=_DAY_SECONDS,
            error_message=MSG_CHAT_MESSAGES_DAILY,
            log_event="chat.messages.rate_limit",
            tenant_id=str(tenant_id),
        )
    except RateLimitError:
        if max_per_user_day is not None and max_per_user_day > 0:
            await redis.decrby(user_key, 1)
        raise


async def check_channel_messages_rate(
    redis: Any,
    *,
    tenant_id: UUID,
    customer_identifier: str,
    max_per_hour: int | None,
) -> bool:
    """True si permitido; False si se supera el tope (sin excepcion — webhook/job)."""
    if max_per_hour is None:
        return True
    if max_per_hour <= 0:
        return False

    key = f"rate:channel_msg:{tenant_id}:{customer_identifier}:{_utc_hour_key()}"
    count = int(await redis.incr(key))
    if count == 1:
        await redis.expire(key, _HOUR_SECONDS)
    if count > max_per_hour:
        await redis.decr(key)
        logger.warning(
            "channel.rate_limit",
            tenant_id=str(tenant_id),
            customer=customer_identifier,
            max_per_hour=max_per_hour,
        )
        return False
    return True


async def check_voice_notes_rate(
    redis: Any,
    *,
    tenant_id: UUID,
    user_id: UUID,
    max_per_hour: int | None,
) -> None:
    key = f"rate:voice_notes:{tenant_id}:{user_id}:{_utc_hour_key()}"
    from app.core.plan_limits import MSG_VOICE_NOTES_HOURLY

    await increment_quota(
        redis,
        key=key,
        delta=1,
        max_count=max_per_hour,
        ttl_seconds=_HOUR_SECONDS,
        error_message=MSG_VOICE_NOTES_HOURLY,
        log_event="voice.rate_limit",
        tenant_id=str(tenant_id),
        user_id=str(user_id),
    )
