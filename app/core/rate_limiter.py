"""Rate limiter Redis para cuotas por tenant (Paso04 / Paso18).

Patron INCRBY + TTL con incremento especulativo y rollback si se supera el tope.
Ventanas cortas (dia/hora); agregados mensuales en ``usage_meter``.
El "día" de las cuotas diarias es el día local de la app (España): se
reinician a las 00:00 hora local (``local_day_key`` / ``daily_ttl_seconds``).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID

import structlog

from app.core.datetime_display import resolve_display_timezone
from app.core.errors import RateLimitError
from app.core.log_redaction import pseudonymize

logger = structlog.get_logger(__name__)

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


# Margen sobre la medianoche local: la clave lleva la fecha, así que el TTL solo
# limpia Redis; nunca debe caducar antes de que termine el día (días de 23/25 h).
_DAY_TTL_MARGIN_SECONDS: int = 3600


def local_day_key(now: datetime | None = None) -> str:
    """Fecha del día de cuota (YYYY-MM-DD) en la zona de la app (España).

    Los límites diarios se reinician a las 00:00 hora local, no a las 00:00 UTC.
    """
    current = now or datetime.now(UTC)
    return current.astimezone(resolve_display_timezone()).strftime("%Y-%m-%d")


def daily_ttl_seconds(now: datetime | None = None) -> int:
    """Segundos hasta la próxima medianoche local + margen (TTL de claves diarias).

    Se calcula en UTC: restar dos datetimes con la misma ZoneInfo usa hora de
    reloj e ignora el cambio de horario (el día de 25 h saldría 24 h).
    """
    tz = resolve_display_timezone()
    current = (now or datetime.now(UTC)).astimezone(tz)
    next_midnight = datetime.combine(current.date() + timedelta(days=1), time(0), tzinfo=tz)
    remaining = next_midnight.astimezone(UTC) - current.astimezone(UTC)
    return int(remaining.total_seconds()) + _DAY_TTL_MARGIN_SECONDS


# Claves de contadores diarios por tenant: únicas para el control de cuota y
# para mostrar el consumo en Mi cuenta (plan_quota_service.get_limit_usage).
def documents_upload_key(tenant_id: UUID, now: datetime | None = None) -> str:
    return f"rate:documents_upload:{tenant_id}:{local_day_key(now)}"


def knowledge_upload_key(tenant_id: UUID, now: datetime | None = None) -> str:
    return f"rate:knowledge_upload:{tenant_id}:{local_day_key(now)}"


def support_requests_key(tenant_id: UUID, now: datetime | None = None) -> str:
    return f"rate:support_requests:{tenant_id}:{local_day_key(now)}"


def _utc_hour_key() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H")


async def check_documents_upload_rate(
    redis: Any,
    *,
    tenant_id: UUID,
    max_per_day: int | None,
    n_files: int = 1,
) -> None:
    key = documents_upload_key(tenant_id)
    from app.core.plan_limits import MSG_DOCUMENTS_DAILY

    await increment_quota(
        redis,
        key=key,
        delta=n_files,
        max_count=max_per_day,
        ttl_seconds=daily_ttl_seconds(),
        error_message=MSG_DOCUMENTS_DAILY,
        log_event="documents.upload.rate_limit",
        tenant_id=str(tenant_id),
        n_files=n_files,
    )


async def check_knowledge_upload_rate(
    redis: Any,
    *,
    tenant_id: UUID,
    max_per_day: int | None,
    n_files: int = 1,
) -> None:
    key = knowledge_upload_key(tenant_id)
    from app.core.plan_limits import MSG_KNOWLEDGE_UPLOADS_DAILY

    await increment_quota(
        redis,
        key=key,
        delta=n_files,
        max_count=max_per_day,
        ttl_seconds=daily_ttl_seconds(),
        error_message=MSG_KNOWLEDGE_UPLOADS_DAILY,
        log_event="knowledge.upload.rate_limit",
        tenant_id=str(tenant_id),
        n_files=n_files,
    )


def _utc_minute_key() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M")


async def check_chat_rate(
    redis: Any,
    *,
    tenant_id: UUID,
    user_id: UUID,
    per_minute: int,
    per_hour: int,
) -> None:
    """Límite de ritmo del chat por usuario dentro de su tenant (D023).

    Ventanas fijas de minuto y de hora. Un usuario de varias organizaciones tiene un
    contador por cada una. ``0`` desactiva la ventana. Si la hora rechaza, se
    devuelve el minuto ya contado para no penalizar dos veces.
    """
    from app.core.plan_limits import MSG_CHAT_RATE

    base = f"rate:chat:{tenant_id}:{user_id}"
    minute_key = f"{base}:m:{_utc_minute_key()}"
    await increment_quota(
        redis,
        key=minute_key,
        delta=1,
        max_count=per_minute if per_minute > 0 else None,
        ttl_seconds=120,
        error_message=MSG_CHAT_RATE,
        log_event="chat.rate_limit",
        tenant_id=str(tenant_id),
        window="minute",
    )
    try:
        await increment_quota(
            redis,
            key=f"{base}:h:{_utc_hour_key()}",
            delta=1,
            max_count=per_hour if per_hour > 0 else None,
            ttl_seconds=_HOUR_SECONDS + 300,
            error_message=MSG_CHAT_RATE,
            log_event="chat.rate_limit",
            tenant_id=str(tenant_id),
            window="hour",
        )
    except RateLimitError:
        if per_minute > 0:
            await redis.decrby(minute_key, 1)
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
            customer_ref=pseudonymize(customer_identifier),
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
