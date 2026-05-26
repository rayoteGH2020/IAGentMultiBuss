"""Rutas web de integraciones externas (Google Calendar, Paso 17)."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.calendar_datetime import (
    format_week_range_label,
    local_input_to_google_iso,
    parse_week_start,
    shift_week_start,
)
from app.core.datetime_display import display_today
from app.core.db import set_tenant_context
from app.core.errors import (
    AppError,
    AuthError,
    ExternalServiceError,
    ForbiddenError,
    ValidationError,
)
from app.core.google_calendar_client import GoogleCalendarClient, build_auth_url
from app.core.oauth_state import consume_state, generate_state
from app.core.templating import render
from app.deps import CurrentTenant, CurrentUser, RedisDep, get_db, get_db_no_tenant
from app.models.calendar_integration import CalendarIntegrationStatus
from app.schemas.calendar import CalendarEventCreate, CalendarEventUpdate
from app.services import calendar_service
from app.services.audit_service import AuditRequestContext

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/settings/integrations", tags=["integrations"])
auth_router = APIRouter(prefix="/auth", tags=["auth"])


def _google_oauth_configured() -> bool:
    settings = get_settings()
    client_id = settings.google_oauth_client_id.strip()
    client_secret = settings.google_oauth_client_secret.get_secret_value().strip()
    return bool(client_id and client_secret)


def _oauth_redirect_uri() -> str:
    base = get_settings().app_base_url.rstrip("/")
    return f"{base}/auth/google/callback"


def _audit_request_context(request: Request) -> AuditRequestContext:
    client = request.client
    ip = client.host if client else None
    return AuditRequestContext(ip=ip, user_agent=request.headers.get("user-agent"))


async def _integration_card_ctx(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
) -> dict[str, object]:
    integration = await calendar_service.get_integration(db, tenant_id, user_id)
    is_connected = (
        integration is not None and integration.status == CalendarIntegrationStatus.active.value
    )
    return {
        "integration": integration,
        "oauth_configured": _google_oauth_configured(),
        "is_connected": is_connected,
    }


async def _calendar_events_ctx(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    week_start: date,
    success_message: str | None = None,
    error_message: str | None = None,
) -> dict[str, object]:
    ctx = await _integration_card_ctx(db, tenant_id=tenant_id, user_id=user_id)
    today = display_today()
    week_start_iso = week_start.isoformat()
    ctx["events"] = []
    ctx["week_start"] = week_start_iso
    ctx["week_start_prev"] = shift_week_start(week_start, -1).isoformat()
    ctx["week_start_next"] = shift_week_start(week_start, 1).isoformat()
    ctx["today_week_start"] = today.isoformat()
    ctx["is_current_week"] = week_start == today
    ctx["week_range_label"] = format_week_range_label(week_start)
    ctx["success_message"] = success_message
    ctx["error_message"] = error_message
    if ctx["is_connected"]:
        try:
            ctx["events"] = await calendar_service.list_events_for_week(
                db,
                tenant_id,
                user_id,
                week_start,
                max_results=50,
            )
        except AppError as exc:
            ctx["error_message"] = exc.message
            logger.warning(
                "calendar.events.list_failed",
                tenant_id=str(tenant_id),
                user_id=str(user_id),
                error=str(exc),
            )
        except Exception as exc:
            ctx["error_message"] = "No se pudieron cargar los eventos del calendario."
            logger.exception(
                "calendar.events.list_failed",
                tenant_id=str(tenant_id),
                user_id=str(user_id),
                error=str(exc),
            )
    return ctx


def _resolve_week_start(week_start_param: str | None) -> date:
    try:
        return parse_week_start(week_start_param)
    except ValidationError:
        return display_today()


def _event_payload_from_form(
    *,
    summary: str,
    start_local: str,
    end_local: str,
    description: str | None,
) -> CalendarEventCreate:
    start_iso = local_input_to_google_iso(start_local)
    end_iso = local_input_to_google_iso(end_local)
    if end_iso <= start_iso:
        raise ValidationError("La fecha de fin debe ser posterior al inicio")
    desc = description.strip() if description and description.strip() else None
    title = summary.strip()
    if not title:
        raise ValidationError("El título es obligatorio")
    return CalendarEventCreate(
        summary=title,
        description=desc,
        start=start_iso,
        end=end_iso,
    )


@router.get("")
async def integrations_index(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
    connected: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    ctx = await _integration_card_ctx(db, tenant_id=tenant.id, user_id=user.id)
    ctx["connected_flag"] = connected
    ctx["error_code"] = error
    return render(
        request,
        full="pages/settings/integrations.html",
        partial="pages/settings/integrations.html",
        ctx=ctx,
    )


@router.get("/google/connect", response_model=None)
async def google_calendar_connect(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    redis: RedisDep,
) -> RedirectResponse | Response:
    if not _google_oauth_configured():
        raise ValidationError("Google OAuth is not configured")

    settings = get_settings()
    state = await generate_state(redis, user.id, tenant.id)
    auth_url = build_auth_url(
        client_id=settings.google_oauth_client_id,
        redirect_uri=_oauth_redirect_uri(),
        state=state,
        scopes=settings.google_calendar_scopes,
    )
    logger.info(
        "calendar.oauth.start",
        tenant_id=str(tenant.id),
        user_id=str(user.id),
    )
    # HTMX boost no sigue 302 a dominios externos; forzar navegación completa.
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=200, headers={"HX-Redirect": auth_url})
    return RedirectResponse(url=auth_url, status_code=302)


@router.post("/google/disconnect")
async def google_calendar_disconnect(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    await calendar_service.revoke_integration(
        db,
        tenant.id,
        user.id,
        request_ctx=_audit_request_context(request),
    )
    ctx = await _integration_card_ctx(db, tenant_id=tenant.id, user_id=user.id)
    return render(
        request,
        full="components/integration_google_calendar.html",
        partial="components/integration_google_calendar.html",
        ctx=ctx,
    )


@router.get("/google/status")
async def google_calendar_status(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    ctx = await _integration_card_ctx(db, tenant_id=tenant.id, user_id=user.id)
    return render(
        request,
        full="components/integration_google_calendar.html",
        partial="components/integration_google_calendar.html",
        ctx=ctx,
    )


@router.get("/google/events")
async def google_calendar_events(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
    week_start: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    ctx = await _calendar_events_ctx(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        week_start=_resolve_week_start(week_start),
    )
    return render(
        request,
        full="components/calendar_events_panel.html",
        partial="components/calendar_events_panel.html",
        ctx=ctx,
    )


@router.post("/google/events")
async def google_calendar_create_event(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
    summary: Annotated[str, Form()] = "",
    description: Annotated[str | None, Form()] = None,
    start_local: Annotated[str, Form(alias="start")] = "",
    end_local: Annotated[str, Form(alias="end")] = "",
    week_start: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    resolved_week = _resolve_week_start(week_start)
    success_message: str | None = None
    error_message: str | None = None
    try:
        payload = _event_payload_from_form(
            summary=summary,
            start_local=start_local,
            end_local=end_local,
            description=description,
        )
        await calendar_service.create_calendar_event(
            db,
            tenant.id,
            user.id,
            payload,
        )
        success_message = "Evento creado en Google Calendar."
    except AppError as exc:
        error_message = exc.message
    ctx = await _calendar_events_ctx(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        week_start=resolved_week,
        success_message=success_message,
        error_message=error_message,
    )
    return render(
        request,
        full="components/calendar_events_panel.html",
        partial="components/calendar_events_panel.html",
        ctx=ctx,
    )


@router.post("/google/events/{event_id}")
async def google_calendar_update_event(
    request: Request,
    event_id: str,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
    summary: Annotated[str, Form()] = "",
    description: Annotated[str | None, Form()] = None,
    start_local: Annotated[str, Form(alias="start")] = "",
    end_local: Annotated[str, Form(alias="end")] = "",
    week_start: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    resolved_week = _resolve_week_start(week_start)
    success_message: str | None = None
    error_message: str | None = None
    try:
        payload = _event_payload_from_form(
            summary=summary,
            start_local=start_local,
            end_local=end_local,
            description=description,
        )
        await calendar_service.update_calendar_event(
            db,
            tenant.id,
            user.id,
            event_id,
            CalendarEventUpdate.model_validate(payload.model_dump()),
        )
        success_message = "Evento actualizado."
    except AppError as exc:
        error_message = exc.message
    ctx = await _calendar_events_ctx(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        week_start=resolved_week,
        success_message=success_message,
        error_message=error_message,
    )
    return render(
        request,
        full="components/calendar_events_panel.html",
        partial="components/calendar_events_panel.html",
        ctx=ctx,
    )


@auth_router.get("/google/callback", include_in_schema=False)
async def google_oauth_callback(
    request: Request,
    code: Annotated[str, Query()],
    state: Annotated[str, Query()],
    redis: RedisDep,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> RedirectResponse:
    """Callback OAuth público; identidad desde state Redis (no JWT)."""
    ctx = await consume_state(redis, state)
    if ctx is None:
        logger.warning("calendar.oauth.error", reason="invalid_or_expired_state")
        return RedirectResponse(
            url="/settings/integrations?error=oauth_state",
            status_code=302,
        )

    if not _google_oauth_configured():
        logger.warning("calendar.oauth.error", reason="oauth_not_configured")
        return RedirectResponse(
            url="/settings/integrations?error=oauth_not_configured",
            status_code=302,
        )

    tenant_id = UUID(ctx["tenant_id"])
    user_id = UUID(ctx["user_id"])
    await set_tenant_context(db, str(tenant_id))

    settings = get_settings()
    try:
        async with GoogleCalendarClient(settings) as client:
            token_resp = await client.exchange_code(code, _oauth_redirect_uri())
            google_email = await client.get_user_email(token_resp.access_token)

        await calendar_service.save_integration(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            token_response=token_resp,
            google_email=google_email,
            request_ctx=_audit_request_context(request),
        )
    except ValidationError as exc:
        logger.warning(
            "calendar.oauth.error",
            reason="validation_error",
            tenant_id=str(tenant_id),
            user_id=str(user_id),
            error=str(exc),
            details=exc.details,
        )
        error_code = "encryption_key" if "ENCRYPTION_KEY" in exc.message else "oauth_failed"
        return RedirectResponse(
            url=f"/settings/integrations?error={error_code}",
            status_code=302,
        )
    except (AuthError, ExternalServiceError, ForbiddenError) as exc:
        logger.warning(
            "calendar.oauth.error",
            reason="google_api_error",
            tenant_id=str(tenant_id),
            user_id=str(user_id),
            error_type=type(exc).__name__,
            error=str(exc),
            details=getattr(exc, "details", {}),
        )
        return RedirectResponse(
            url="/settings/integrations?error=oauth_failed",
            status_code=302,
        )

    logger.info(
        "calendar.oauth.success",
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        google_email=google_email,
    )
    return RedirectResponse(
        url="/settings/integrations?connected=google",
        status_code=302,
    )
