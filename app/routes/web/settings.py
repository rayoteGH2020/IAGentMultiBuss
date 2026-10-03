from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email import EmailAttachment
from app.core.errors import AppError, ExternalServiceError, RateLimitError, ValidationError
from app.core.support_uploads import (
    ERR_CONTENT,
    ERR_EMPTY,
    ERR_EXTENSION,
    ERR_TOO_LARGE,
    SUPPORT_ATTACHMENT_ACCEPT,
    SUPPORT_ATTACHMENT_MAX_BYTES,
    SupportAttachmentError,
    validate_support_attachment,
)
from app.core.templating import render
from app.core.uploads import UploadValidationError, read_upload_limited
from app.deps import (
    CurrentTenant,
    CurrentUser,
    EntitlementsDep,
    RedisDep,
    RequireOrgAdmin,
    get_db,
)
from app.routes.web.audit_context import audit_request_context
from app.schemas.support import (
    SUPPORT_KIND_LABELS,
    SUPPORT_MESSAGE_MAX_LENGTH,
    SUPPORT_SEVERITY_LABELS,
    SUPPORT_TITLE_MAX_LENGTH,
    SupportRequestCreate,
)
from app.services import (
    entitlement_service,
    plan_quota_service,
    quota_status_service,
    support_service,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def settings_redirect() -> RedirectResponse:
    return RedirectResponse(url="/settings/profile", status_code=302)


@router.get("/profile")
async def settings_profile(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    ents: EntitlementsDep,
    redis: RedisDep,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Mi cuenta: datos personales + organización (plan, límites y consumo del mes)."""
    usage = await plan_quota_service.get_limit_usage(db, redis, ents, tenant.id)
    return render(
        request,
        full="pages/settings/profile.html",
        ctx={
            "user": user,
            "tenant": tenant,
            "plan": entitlement_service.build_plan_summary(ents, usage),
            "usage": await plan_quota_service.get_usage_snapshot(db, ents, tenant.id),
            "quota_statuses": await quota_status_service.monthly_statuses(db, ents, tenant.id),
            "ai_usage_percent": await quota_status_service.ai_usage_percent(db, ents, tenant.id),
        },
    )


@router.get("/organization")
async def settings_organization() -> RedirectResponse:
    # Los datos de organización se muestran ahora dentro de «Mi cuenta».
    return RedirectResponse(url="/settings/profile", status_code=302)


@router.get("/billing")
async def settings_billing() -> RedirectResponse:
    # Plan y consumo unificados en «Mi cuenta» (enlaces antiguos siguen funcionando).
    return RedirectResponse(url="/settings/profile", status_code=302)


_SUPPORT_PAGE = "pages/settings/support.html"
_SUPPORT_FORM = "components/support/support_form.html"

_SUPPORT_FIELD_LABELS: dict[str, str] = {
    "title": "Título",
    "message": "Mensaje",
    "kind": "Tipo",
    "severity": "Gravedad",
}

_SUPPORT_ERRORS: dict[str, str] = {
    ERR_EMPTY: "El adjunto está vacío.",
    ERR_TOO_LARGE: "El adjunto supera el máximo de 2 MB.",
    ERR_EXTENSION: "Formato de adjunto no admitido. Usa Word (.docx), texto (.txt) o JPG.",
    ERR_CONTENT: "El contenido del adjunto no corresponde a su formato.",
    "email_sadm_missing": "Falta configurar EMAIL_SADM. Avisa al superadmin por otro canal.",
    "smtp_not_configured": "Falta configurar SMTP. Avisa al superadmin por otro canal.",
}
_SUPPORT_RATE_LIMITED = "Has alcanzado el máximo de mensajes de soporte por hoy."
_SUPPORT_SEND_FAILED = "No se pudo enviar el mensaje. Inténtalo de nuevo en unos minutos."


def _render_support(
    request: Request,
    *,
    values: dict[str, str] | None = None,
    error: str | None = None,
    sent: bool = False,
) -> HTMLResponse:
    ctx: dict[str, Any] = {
        "kinds": SUPPORT_KIND_LABELS,
        "severities": SUPPORT_SEVERITY_LABELS,
        "title_max": SUPPORT_TITLE_MAX_LENGTH,
        "message_max": SUPPORT_MESSAGE_MAX_LENGTH,
        "attachment_accept": SUPPORT_ATTACHMENT_ACCEPT,
        "values": values or {},
        "error": error,
        "sent": sent,
    }
    return render(request, full=_SUPPORT_PAGE, partial=_SUPPORT_FORM, ctx=ctx)


def _support_error(exc: AppError) -> str:
    if isinstance(exc, RateLimitError):
        return _SUPPORT_RATE_LIMITED
    code = exc.details.get("code") if isinstance(exc.details, dict) else None
    return _SUPPORT_ERRORS.get(str(code), _SUPPORT_SEND_FAILED)


def _support_validation_error(exc: PydanticValidationError) -> str:
    fields = sorted(
        {_SUPPORT_FIELD_LABELS.get(str(err["loc"][0]), str(err["loc"][0])) for err in exc.errors()}
    )
    return f"Revisa estos campos: {', '.join(fields)}."


async def _read_attachment(upload: UploadFile | None) -> EmailAttachment | None:
    """None si no se adjuntó nada (el input vacío llega con filename vacío)."""
    if upload is None or not upload.filename:
        return None
    try:
        data = await read_upload_limited(upload, max_bytes=SUPPORT_ATTACHMENT_MAX_BYTES)
    except UploadValidationError as exc:
        raise SupportAttachmentError(ERR_TOO_LARGE) from exc
    return validate_support_attachment(upload.filename, data)


@router.get("/support")
async def settings_support(request: Request, _: RequireOrgAdmin) -> HTMLResponse:
    """Soporte técnico: formulario del admin del negocio para escribir al SADM."""
    return _render_support(request)


@router.post("/support")
async def settings_support_send(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    membership: RequireOrgAdmin,
    redis: RedisDep,
    db: AsyncSession = Depends(get_db),
    title: str = Form(""),
    message: str = Form(""),
    kind: str = Form(""),
    severity: str = Form(""),
    attachment: UploadFile | None = File(None),
) -> HTMLResponse:
    """Envía la incidencia por email al SADM. Errores: 200 con el formulario y el mensaje."""
    values = {"title": title, "message": message, "kind": kind, "severity": severity}
    try:
        payload = SupportRequestCreate.model_validate(
            {"title": title, "message": message, "kind": kind, "severity": severity}
        )
    except PydanticValidationError as exc:
        return _render_support(request, values=values, error=_support_validation_error(exc))
    try:
        email_attachment = await _read_attachment(attachment)
    except SupportAttachmentError as exc:
        return _render_support(request, values=values, error=_SUPPORT_ERRORS[exc.code])
    try:
        await support_service.send_support_request(
            db,
            tenant=tenant,
            user=user,
            actor_role=membership.role,
            payload=payload,
            attachment=email_attachment,
            redis=redis,
            request_ctx=audit_request_context(request),
        )
    except (ValidationError, ExternalServiceError, RateLimitError) as exc:
        return _render_support(request, values=values, error=_support_error(exc))
    return _render_support(request, sent=True)
