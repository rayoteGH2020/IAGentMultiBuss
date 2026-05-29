"""Jobs ARQ para procesado de mensajes de canales externos (Paso 21 E/F).

Flujo por job:
  1. Cargar tenant e integración desde BD (contexto RLS).
  2. Generar respuesta con channel_chat_service.answer_for_channel().
  3. Commit de mensajes persistidos.
  4. Enviar respuesta o escalar según confianza.

Gestión de errores (ver diseño en memory/project_paso21_status.md):
  - 1er fallo: notificar al cliente "procesando" y reintentar (ARQ max_tries=2).
  - 2º fallo: mensaje de error amigable, sin relanzar excepción.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.core import telegram_client, whatsapp_client
from app.core.cache import get_redis
from app.core.db import session_factory_for_worker
from app.core.email import send_email
from app.models.channel_integration import ChannelIntegrationStatus
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.user import User
from app.services import audit_service, channel_chat_service, channel_integration_service

logger = structlog.get_logger(__name__)

_PROCESSING_MSG = "Estamos procesando tu consulta. Te responderemos en breve."
_ERROR_MSG = (
    "Lo siento, estamos teniendo dificultades técnicas en este momento. "
    "Por favor, inténtalo de nuevo más tarde o contacta con nosotros directamente."
)


async def _send_channel_message(
    channel: str,
    customer_identifier: str,
    text: str,
    *,
    api_token: str,
    phone_number_id: str | None,
) -> None:
    """Envía un mensaje al canal externo correspondiente."""
    if channel == "whatsapp":
        if not phone_number_id:
            logger.warning("channel.send.missing_phone_number_id")
            return
        await whatsapp_client.send_text_message(
            to=customer_identifier,
            text=text,
            phone_number_id=phone_number_id,
            api_token=api_token,
        )
    elif channel == "telegram":
        await telegram_client.send_message(
            bot_token=api_token,
            chat_id=customer_identifier,
            text=text,
        )
    else:
        logger.warning("channel.unknown_channel", channel=channel)


async def _safe_send(
    channel: str,
    customer_identifier: str,
    text: str,
    *,
    api_token: str | None,
    phone_number_id: str | None,
) -> None:
    """Envío silencioso: loguea pero no lanza si el envío falla."""
    if not api_token:
        return
    try:
        await _send_channel_message(
            channel,
            customer_identifier,
            text,
            api_token=api_token,
            phone_number_id=phone_number_id,
        )
    except Exception:
        logger.exception("channel.send_failed", channel=channel, customer=customer_identifier)


async def _check_rate_limit(
    redis_conn: Any,
    *,
    tenant_id: str,
    customer_identifier: str,
) -> bool:
    """Token bucket horario por customer_identifier.

    Retorna True si el mensaje está permitido, False si se ha superado el límite.
    La clave incluye la hora UTC actual: expira automáticamente a la hora siguiente.
    """
    settings = get_settings()
    hour = datetime.now(UTC).strftime("%Y-%m-%dT%H")
    key = f"rate:channel_msg:{tenant_id}:{customer_identifier}:{hour}"
    count = int(await redis_conn.incr(key))
    if count == 1:
        await redis_conn.expire(key, 3600)
    return count <= settings.channel_rate_limit_msg_per_hour


async def _get_admin_email(db: Any, tenant_id: uuid.UUID) -> str | None:
    stmt = (
        select(User.email)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.tenant_id == tenant_id, Membership.role == "admin")
        .limit(1)
    )
    result = await db.execute(stmt)
    value: str | None = result.scalar_one_or_none()
    return value


async def process_channel_message(
    ctx: dict[str, Any],
    tenant_id: str,
    channel: str,
    customer_identifier: str,
    message_text: str,
    integration_id: str,
) -> None:
    """Job ARQ: genera respuesta RAG y la envía al canal externo.

    Args llegan como str porque ARQ serializa a JSON; se convierten a UUID aquí.
    """
    _ = integration_id  # guardado para trazabilidad futura / Sub-módulo F
    job_try: int = ctx.get("job_try", 1)
    tenant_uuid = uuid.UUID(tenant_id)
    redis_conn = ctx.get("redis") or get_redis()

    # Variables resueltas en la sesión BD; usadas después en el envío
    api_token: str | None = None
    phone_number_id: str | None = None
    company_name: str = "la empresa"
    confidence_threshold: float = get_settings().channel_confidence_threshold_default
    admin_email: str | None = None
    response_text: str | None = None
    response_confidence: float = 0.0

    try:
        async with session_factory_for_worker(tenant_uuid) as db:
            # Cargar tenant
            tenant = (
                await db.execute(select(Tenant).where(Tenant.id == tenant_uuid))
            ).scalar_one_or_none()
            if tenant is None:
                logger.error("channel.job.tenant_not_found", tenant_id=tenant_id)
                return
            company_name = tenant.name

            # Cargar integración activa
            integration = await channel_integration_service.get_integration(
                db, tenant_uuid, channel
            )
            if integration is None or integration.status != ChannelIntegrationStatus.active.value:
                logger.warning(
                    "channel.job.integration_inactive",
                    tenant_id=tenant_id,
                    channel=channel,
                )
                return
            confidence_threshold = integration.confidence_threshold
            api_token = channel_integration_service.decrypt_api_token(integration)
            phone_number_id = integration.phone_number_id

            # Email de escalado (admin del tenant)
            admin_email = await _get_admin_email(db, tenant_uuid)

            # Audit log: mensaje recibido
            await audit_service.log_action(
                db,
                tenant_id=tenant_uuid,
                user_id=None,
                action="channel.message_received",
                resource_type="channel_conversation",
                resource_id=None,
                metadata={
                    "channel": channel,
                    "customer_identifier": customer_identifier,
                    "chars": len(message_text),
                },
            )

            # Rate-limit: máx. N mensajes/hora por customer_identifier
            if not await _check_rate_limit(
                redis_conn, tenant_id=tenant_id, customer_identifier=customer_identifier
            ):
                limit_msg = (
                    "Hemos recibido demasiados mensajes. "
                    "Por favor, espera un momento antes de volver a escribirnos."
                )
                await db.commit()
                await _safe_send(
                    channel,
                    customer_identifier,
                    limit_msg,
                    api_token=api_token,
                    phone_number_id=phone_number_id,
                )
                logger.warning(
                    "channel.rate_limited",
                    tenant_id=tenant_id,
                    channel=channel,
                    customer_identifier=customer_identifier,
                    limit=get_settings().channel_rate_limit_msg_per_hour,
                )
                return

            # Generar respuesta RAG
            response = await channel_chat_service.answer_for_channel(
                db,
                tenant,
                channel=channel,
                customer_identifier=customer_identifier,
                message_text=message_text,
            )
            response_text = response.text
            response_confidence = response.confidence

            # Commit: persiste Conversation + ChannelMessages del turno
            await db.commit()

    except Exception:
        logger.exception(
            "channel.job.failed",
            tenant_id=tenant_id,
            channel=channel,
            job_try=job_try,
        )
        if job_try < 2:
            # 1er fallo: notificar "procesando" y dejar que ARQ reintente
            await _safe_send(
                channel,
                customer_identifier,
                _PROCESSING_MSG,
                api_token=api_token,
                phone_number_id=phone_number_id,
            )
            raise  # ARQ reintentará → job_try = 2

        # 2º fallo: error definitivo, sin relanzar
        error_text = f"{_ERROR_MSG.replace('nosotros', company_name)}"
        await _safe_send(
            channel,
            customer_identifier,
            error_text,
            api_token=api_token,
            phone_number_id=phone_number_id,
        )
        return

    # Envío de respuesta o escalado (fuera de la sesión BD)
    assert response_text is not None, "response_text debe estar asignado si no hubo excepción"

    if response_confidence >= confidence_threshold:
        await _safe_send(
            channel,
            customer_identifier,
            response_text,
            api_token=api_token,
            phone_number_id=phone_number_id,
        )
        logger.info(
            "channel.message_sent",
            tenant_id=tenant_id,
            channel=channel,
            confidence=response_confidence,
        )
    else:
        # Confianza insuficiente → escalar
        escalation_text = (
            f"Lo siento, no tengo información sobre eso. "
            f"Te recomiendo contactar directamente con {company_name}."
        )
        await _safe_send(
            channel,
            customer_identifier,
            escalation_text,
            api_token=api_token,
            phone_number_id=phone_number_id,
        )
        # Notificar al negocio por email (solución temporal — ver deuda técnica)
        if admin_email:
            try:
                await send_email(
                    to=admin_email,
                    subject=f"[Canal externo] Consulta sin respuesta — {company_name}",
                    body=(
                        f"Un cliente de {channel} ({customer_identifier}) preguntó:\n\n"
                        f'"{message_text}"\n\n'
                        f"El asistente no pudo responder automáticamente (confianza: "
                        f"{response_confidence:.2f}, umbral: {confidence_threshold:.2f}). "
                        f"Por favor, responde manualmente."
                    ),
                )
            except Exception:
                logger.exception("channel.escalation_email_failed", to=admin_email)
        logger.info(
            "channel.escalated",
            tenant_id=tenant_id,
            channel=channel,
            confidence=response_confidence,
            threshold=confidence_threshold,
        )
