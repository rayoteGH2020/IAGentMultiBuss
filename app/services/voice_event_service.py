"""Servicio de voz → Google Calendar (Paso 23).

Orquesta: validar audio → rate-limit → transcribir → extraer borrador → confirmar.
No conoce HTTP; recibe bytes/Pydantic y devuelve Pydantic (AGENTS.md §3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import structlog

from app.config import get_settings
from app.core.errors import NotFoundError, RateLimitError, ValidationError
from app.core.uploads import UploadValidationError, validate_voice_upload
from app.llm import voice_calendar
from app.models.calendar_integration import CalendarIntegrationStatus
from app.schemas.calendar import CalendarEventCreate, VoiceEventDraft
from app.services import audit_service, calendar_service
from app.services.audit_service import (
    ACTION_CALENDAR_EVENT_CREATED_FROM_VOICE,
    ACTION_CALENDAR_VOICE_TRANSCRIBED,
    RESOURCE_CALENDAR_EVENT,
    RESOURCE_VOICE_TRANSCRIPTION,
    AuditRequestContext,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

# Clave Redis para el rate-limit de voz por (tenant_id, user_id) por hora.
# Ventana: 1 hora deslizante usando INCRBY + TTL (mismo patrón que knowledge upload).
_RATE_KEY_TEMPLATE = "rate:voice_calendar:{tenant_id}:{user_id}:{hour}"
_HOUR_SECONDS: int = 3600

# Recordatorios que se inyectan en TODOS los eventos creados por voz (objetivo #5).
# Definidos aquí como constante de módulo para que el invariante sea
# comprobable en tests sin depender de la implementación interna de confirm_event.
VOICE_REMINDERS: list[dict[str, object]] = [
    {"method": "popup", "minutes": 1440},  # 24 h antes
    {"method": "popup", "minutes": 60},  # 1 h antes
]


async def _check_voice_rate_limit(
    redis: Any,
    *,
    tenant_id: UUID,
    user_id: UUID,
    max_per_hour: int,
) -> None:
    """Verifica y contabiliza la cuota horaria de notas de voz por usuario.

    Incrementa especulativamente el contador; si supera el límite lo revierte
    y lanza RateLimitError.
    """
    hour_str = datetime.now(UTC).strftime("%Y-%m-%d-%H")
    key = _RATE_KEY_TEMPLATE.format(
        tenant_id=tenant_id,
        user_id=user_id,
        hour=hour_str,
    )
    new_count = int(await redis.incrby(key, 1))
    if new_count == 1:
        await redis.expire(key, _HOUR_SECONDS)

    if new_count > max_per_hour:
        await redis.decrby(key, 1)
        logger.warning(
            "voice.rate_limit",
            tenant_id=str(tenant_id),
            user_id=str(user_id),
            max_per_hour=max_per_hour,
        )
        raise RateLimitError(
            f"Has superado el límite de {max_per_hour} notas de voz por hora. "
            "Inténtalo de nuevo más tarde."
        )


async def draft_from_audio(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    audio: bytes,
    mime_type: str,
    redis: Any,
    request_ctx: AuditRequestContext | None = None,
) -> VoiceEventDraft:
    """Valida, rate-limita, transcribe y extrae el borrador de evento. NO crea nada.

    Args:
        db: Sesión async activa (tenant context ya seteado por middleware).
        tenant_id: Tenant del usuario que dicta.
        user_id: Usuario que dicta (selecciona su calendario).
        audio: Bytes del audio recibidos del cliente (multipart).
        mime_type: MIME declarado por el cliente (se verifica por magic bytes).
        redis: Cliente Redis para rate-limit.
        request_ctx: IP y user-agent para audit log.

    Returns:
        VoiceEventDraft con la propuesta de evento lista para confirmación.

    Raises:
        ValidationError: si voice_calendar está desactivado, el audio es inválido
            o el tipo MIME no está permitido.
        NotFoundError: si el usuario no tiene Google Calendar conectado y activo.
        RateLimitError: si se supera el límite de notas de voz por hora.
        LLMCompleteError: si la transcripción o extracción LLM falla.
    """
    settings = get_settings()

    # 1. Feature flag
    if not settings.voice_calendar_enabled:
        raise ValidationError("La función de dictado por voz no está disponible en este momento.")

    # 2. Verificar integración activa
    integration = await calendar_service.get_integration(db, tenant_id, user_id)
    if integration is None or integration.status != CalendarIntegrationStatus.active.value:
        raise NotFoundError(
            "Google Calendar no está conectado. Ve a Ajustes > Integraciones para vincularlo."
        )

    # 3. Validar audio (MIME real por magic bytes + tamaño)
    try:
        validated_mime = validate_voice_upload(
            audio,
            max_bytes=settings.voice_max_audio_bytes,
        )
    except UploadValidationError as exc:
        raise ValidationError(str(exc)) from exc

    # Usar el MIME validado para la llamada al LLM (más fiable que el declarado)
    mime_to_use = validated_mime

    # 4. Rate-limit por (tenant_id, user_id) por hora
    await _check_voice_rate_limit(
        redis,
        tenant_id=tenant_id,
        user_id=user_id,
        max_per_hour=settings.voice_rate_limit_per_hour,
    )

    # 5. Transcripción
    transcript = await voice_calendar.transcribe_audio(
        audio,
        mime_to_use,
        tenant_id=tenant_id,
        db=db,
    )

    # 6. Extracción estructurada del borrador
    tz = ZoneInfo(settings.voice_calendar_default_timezone)
    now_iso = datetime.now(tz).isoformat(timespec="seconds")

    extraction = await voice_calendar.draft_event_from_transcript(
        transcript,
        now_iso=now_iso,
        timezone=settings.voice_calendar_default_timezone,
        default_duration_min=settings.voice_event_default_duration_minutes,
        tenant_id=tenant_id,
        db=db,
    )

    # 7. Ensamblar VoiceEventDraft: transcript asignado aquí, no por el LLM
    draft = VoiceEventDraft(transcript=transcript, **extraction.model_dump())

    # 8. Audit log (sin guardar el audio; solo metadatos)
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_CALENDAR_VOICE_TRANSCRIBED,
        resource_type=RESOURCE_VOICE_TRANSCRIPTION,
        metadata={
            "audio_bytes": len(audio),
            "mime_type": mime_to_use,
            "confidence": float(draft.confidence),
            "needs_clarification": draft.needs_clarification,
        },
        request_ctx=request_ctx,
    )

    logger.info(
        "voice.draft_ready",
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        confidence=float(draft.confidence),
        needs_clarification=draft.needs_clarification,
    )
    return draft


async def confirm_event(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    event: CalendarEventCreate,
    request_ctx: AuditRequestContext | None = None,
) -> Any:
    """Crea el evento en Google Calendar inyectando siempre VOICE_REMINDERS.

    El servicio sobreescribe `reminders` independientemente de lo que venga
    del formulario de confirmación, garantizando el invariante del objetivo #5
    (24 h + 1 h antes).

    Args:
        db: Sesión async activa.
        tenant_id: Tenant del usuario.
        user_id: Usuario cuyo Google Calendar recibe el evento.
        event: CalendarEventCreate con start/end ya convertidos a ISO 8601
            con offset (local_input_to_google_iso aplicado en la ruta).
        request_ctx: IP y user-agent para audit log.

    Returns:
        CalendarEvent creado (con html_link al evento en Google Calendar).
    """
    # Siempre sobreescribir reminders: VOICE_REMINDERS es el invariante del módulo.
    event_payload = event.model_copy(update={"reminders": VOICE_REMINDERS})

    created = await calendar_service.create_calendar_event(
        db,
        tenant_id,
        user_id,
        event_payload,
    )

    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_CALENDAR_EVENT_CREATED_FROM_VOICE,
        resource_type=RESOURCE_CALENDAR_EVENT,
        metadata={"event_id": created.id, "summary": created.summary},
        request_ctx=request_ctx,
    )

    logger.info(
        "voice.event_created",
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        event_id=created.id,
    )
    return created
