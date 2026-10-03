"""Soporte técnico: el admin del tenant envía una incidencia por email al SADM.

No se persiste nada salvo la entrada de audit_log (sin título ni mensaje: solo
metadatos). El adjunto viaja en memoria hasta el SMTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from app.config import get_settings
from app.core.datetime_display import local_datetime
from app.core.email import EmailAttachment, send_email
from app.core.entitlement_codes import plan_ui_name
from app.core.errors import ExternalServiceError, ValidationError
from app.core.permissions import role_label
from app.core.rate_limiter import daily_ttl_seconds, increment_quota, support_requests_key
from app.core.templating import templates
from app.schemas.support import (
    SUPPORT_KIND_LABELS,
    SUPPORT_SEVERITY_LABELS,
    SupportRequestCreate,
)
from app.services import audit_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.tenant import Tenant
    from app.models.user import User
    from app.services.audit_service import AuditRequestContext

log = structlog.get_logger(__name__)

SUPPORT_REQUESTS_MAX_PER_DAY = 10
SUPPORT_SUBJECT_PREFIX = "[Soporte técnico]"
ACTION_SUPPORT_REQUEST_SENT = "support.request_sent"
RESOURCE_SUPPORT_REQUEST = "support_request"
_EMAIL_TEMPLATE = "emails/support_request.html"


@dataclass(frozen=True, slots=True)
class SupportEmail:
    subject: str
    body: str
    html: str


def build_support_email(
    *,
    tenant: Tenant,
    user: User,
    actor_role: str,
    payload: SupportRequestCreate,
    attachment: EmailAttachment | None,
    sent_at: datetime,
) -> SupportEmail:
    """Asunto, texto plano y HTML del aviso. La gravedad encabeza asunto y cuerpo."""
    severity = SUPPORT_SEVERITY_LABELS[payload.severity].upper()
    kind = SUPPORT_KIND_LABELS[payload.kind]
    org_name = " ".join(tenant.name.split())
    ctx: dict[str, Any] = {
        "severity_code": payload.severity.value,
        "severity": severity,
        "kind": kind,
        "title": payload.title,
        "message": payload.message,
        "org_name": org_name,
        "tenant_id": str(tenant.id),
        "clerk_org_id": tenant.clerk_org_id or "—",
        "plan": plan_ui_name(tenant.plan_code or tenant.plan),
        "member_name": user.name or "—",
        "member_email": user.email,
        "member_role": role_label(actor_role),
        "sent_at": local_datetime(sent_at),
        "attachment_name": attachment.filename if attachment else None,
    }
    subject = f"{SUPPORT_SUBJECT_PREFIX}[{severity}] {payload.title} · {org_name}"
    body = "\n".join(
        [
            f"GRAVEDAD: {severity}",
            f"Tipo: {kind}",
            "",
            f"Título: {payload.title}",
            "",
            "Mensaje:",
            payload.message,
            "",
            "Organización",
            f"  Nombre: {org_name}",
            f"  ID tenant: {tenant.id}",
            f"  Clerk org ID: {ctx['clerk_org_id']}",
            f"  Plan: {ctx['plan']}",
            "",
            "Enviado por",
            f"  Nombre: {ctx['member_name']}",
            f"  Email: {user.email}",
            f"  Rol: {ctx['member_role']}",
            f"  Fecha: {ctx['sent_at']}",
            "",
            f"Adjunto: {ctx['attachment_name'] or 'ninguno'}",
        ]
    )
    html = templates.env.get_template(_EMAIL_TEMPLATE).render(**ctx)
    return SupportEmail(subject=subject, body=body, html=html)


def _sadm_address() -> str:
    """Destino del aviso; falla antes de consumir cupo si falta configuración."""
    settings = get_settings()
    to = settings.email_sadm.strip()
    if not to:
        raise ValidationError(
            "Superadmin notification email is not configured",
            details={"code": "email_sadm_missing"},
        )
    # send_email() omite el envío sin SMTP: aquí sería un falso "mensaje enviado".
    if not settings.smtp_host.strip():
        raise ExternalServiceError(
            "SMTP is not configured", details={"code": "smtp_not_configured"}
        )
    return to


async def send_support_request(
    db: AsyncSession,
    *,
    tenant: Tenant,
    user: User,
    actor_role: str,
    payload: SupportRequestCreate,
    attachment: EmailAttachment | None,
    redis: Any,
    request_ctx: AuditRequestContext | None = None,
) -> None:
    """Envía la incidencia al SADM y la registra en audit_log.

    Raises:
        ValidationError: falta EMAIL_SADM.
        ExternalServiceError: SMTP sin configurar o fallo de envío.
        RateLimitError: el tenant superó el máximo diario de envíos.
    """
    to = _sadm_address()
    key = support_requests_key(tenant.id)
    await increment_quota(
        redis,
        key=key,
        delta=1,
        max_count=SUPPORT_REQUESTS_MAX_PER_DAY,
        ttl_seconds=daily_ttl_seconds(),
        error_message="Daily support request limit reached",
        log_event="support.request.rate_limit",
        tenant_id=str(tenant.id),
    )
    email = build_support_email(
        tenant=tenant,
        user=user,
        actor_role=actor_role,
        payload=payload,
        attachment=attachment,
        sent_at=datetime.now(UTC),
    )
    try:
        await send_email(
            to=to,
            subject=email.subject,
            body=email.body,
            html=email.html,
            attachments=[attachment] if attachment else (),
        )
    except Exception as exc:
        # El envío fallido no consume cupo: el admin puede reintentar.
        await redis.decrby(key, 1)
        log.exception(
            "support.request_failed", error_type=type(exc).__name__, tenant_id=str(tenant.id)
        )
        raise ExternalServiceError(
            "Failed to send support email", details={"code": "support_send_failed"}
        ) from exc

    await audit_service.log_action(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        action=ACTION_SUPPORT_REQUEST_SENT,
        resource_type=RESOURCE_SUPPORT_REQUEST,
        metadata={
            "kind": payload.kind.value,
            "severity": payload.severity.value,
            "has_attachment": attachment is not None,
            "attachment_bytes": len(attachment.data) if attachment else 0,
        },
        request_ctx=request_ctx,
    )
    log.info(
        "support.request_sent",
        tenant_id=str(tenant.id),
        kind=payload.kind.value,
        severity=payload.severity.value,
    )
