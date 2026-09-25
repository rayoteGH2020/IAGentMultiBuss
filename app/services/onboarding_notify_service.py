"""Avisos de onboarding: usuario Clerk sin organización."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

import structlog

from app.config import get_settings
from app.core.email import send_email
from app.core.errors import ExternalServiceError, RateLimitError, ValidationError

if TYPE_CHECKING:
    from uuid import UUID

    from redis.asyncio import Redis

log = structlog.get_logger(__name__)

MISSING_ORG_NOTIFY_SUBJECT = "Falta organización en Clerk"
MISSING_ORG_NOTIFY_TTL_SECONDS = 60 * 60
_REDIS_KEY_PREFIX = "onboarding:missing_org_notify:"


@dataclass(frozen=True, slots=True)
class MissingOrgNotifyResult:
    """Resultado del aviso: SMTP enviado o mailto de fallback."""

    mode: Literal["smtp", "mailto"]
    mailto_url: str | None = None


def missing_org_notify_body(user_email: str) -> str:
    return f"El email {user_email} no tiene organización asignada en Clerk"


def build_missing_org_mailto_url(*, to: str, user_email: str) -> str:
    subject = quote(MISSING_ORG_NOTIFY_SUBJECT)
    body = quote(missing_org_notify_body(user_email))
    return f"mailto:{to}?subject={subject}&body={body}"


async def notify_superadmin_missing_organization(
    *,
    user_email: str,
    user_id: UUID,
    redis: Redis | None,
) -> MissingOrgNotifyResult:
    """Envía al SADM el aviso de usuario sin organización.

    Si SMTP no está configurado, devuelve un ``mailto:`` de fallback (mismo
    destinatario/asunto/cuerpo) para que el usuario complete el envío en su
    cliente de correo.

    Raises:
        ValidationError: falta EMAIL_SADM.
        ExternalServiceError: fallo de envío SMTP.
        RateLimitError: ya se notificó recientemente para este usuario.
    """
    settings = get_settings()
    to = settings.email_sadm.strip()
    if not to:
        raise ValidationError(
            "Superadmin notification email is not configured",
            details={"code": "email_sadm_missing"},
        )

    rate_key = f"{_REDIS_KEY_PREFIX}{user_id}"
    if redis is not None:
        created = await redis.set(
            rate_key,
            "1",
            nx=True,
            ex=MISSING_ORG_NOTIFY_TTL_SECONDS,
        )
        if not created:
            raise RateLimitError(
                "Notification already sent recently",
                details={"code": "missing_org_notify_rate_limited"},
            )

    body = missing_org_notify_body(user_email)
    if not settings.smtp_host.strip():
        mailto_url = build_missing_org_mailto_url(to=to, user_email=user_email)
        log.info(
            "onboarding.missing_org_mailto_fallback",
            user_id=str(user_id),
            to_domain=to.split("@")[-1] if "@" in to else "unknown",
        )
        return MissingOrgNotifyResult(mode="mailto", mailto_url=mailto_url)

    try:
        await send_email(to=to, subject=MISSING_ORG_NOTIFY_SUBJECT, body=body)
    except Exception as exc:
        if redis is not None:
            await redis.delete(rate_key)
        log.exception(
            "onboarding.missing_org_notify_failed",
            user_id=str(user_id),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise ExternalServiceError(
            "Failed to send notification email",
            details={"code": "missing_org_notify_send_failed"},
        ) from exc

    log.info(
        "onboarding.missing_org_notified",
        user_id=str(user_id),
        to_domain=to.split("@")[-1] if "@" in to else "unknown",
    )
    return MissingOrgNotifyResult(mode="smtp")
