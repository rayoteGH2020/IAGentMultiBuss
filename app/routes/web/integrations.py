"""Rutas web de integraciones externas (Google Calendar, Paso 17).

Solo configura la conexión (OAuth conectar/desconectar/estado). La
visualización y gestión de eventos del calendario vive en ``routes/web/calendar.py``.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.db import set_tenant_context
from app.core.errors import (
    AuthError,
    ExternalServiceError,
    ForbiddenError,
    ValidationError,
)
from app.core.google_calendar_client import GoogleCalendarClient, build_auth_url
from app.core.oauth_state import consume_state, generate_state
from app.core.templating import render
from app.deps import CurrentTenant, CurrentUser, RedisDep, get_db, get_db_no_tenant
from app.schemas.calendar import CalendarIntegrationStatus
from app.services import calendar_service, channel_integration_service
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
    ctx["tenant"] = tenant
    ctx["app_base_url"] = get_settings().app_base_url.rstrip("/")

    wa = await channel_integration_service.get_integration(db, tenant.id, "whatsapp")
    tg = await channel_integration_service.get_integration(db, tenant.id, "telegram")
    ctx["wa_integration"] = wa
    ctx["wa_connected"] = wa is not None and wa.status == "active"
    ctx["tg_integration"] = tg
    ctx["tg_connected"] = tg is not None and tg.status == "active"

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
        return RedirectResponse(
            url="/settings/integrations?error=oauth_failed",
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
