"""Rutas web del Calendario: visualización y gestión de eventos de Google Calendar.

La configuración de la conexión (OAuth conectar/desconectar) vive en
``routes/web/integrations.py``. Aquí solo se visualizan y gestionan eventos del
calendario ya conectado del usuario.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import structlog
from fastapi import APIRouter, Depends, Form, Query, Request

if TYPE_CHECKING:
    from datetime import date
    from uuid import UUID

    from fastapi.responses import HTMLResponse
    from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar_datetime import (
    format_week_range_label,
    local_input_to_google_iso,
    parse_week_start,
    shift_week_start,
)
from app.core.datetime_display import display_today
from app.core.errors import AppError, ValidationError
from app.core.templating import render
from app.deps import CurrentTenant, CurrentUser, get_db
from app.models.calendar_integration import CalendarIntegrationStatus
from app.schemas.calendar import CalendarEventCreate, CalendarEventUpdate
from app.services import calendar_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/calendar", tags=["calendar"])


async def _events_ctx(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    week_start: date,
    success_message: str | None = None,
    error_message: str | None = None,
) -> dict[str, object]:
    integration = await calendar_service.get_integration(db, tenant_id, user_id)
    is_connected = (
        integration is not None and integration.status == CalendarIntegrationStatus.active.value
    )
    today = display_today()
    ctx: dict[str, object] = {
        "integration": integration,
        "is_connected": is_connected,
        "events": [],
        "week_start": week_start.isoformat(),
        "week_start_prev": shift_week_start(week_start, -1).isoformat(),
        "week_start_next": shift_week_start(week_start, 1).isoformat(),
        "today_week_start": today.isoformat(),
        "is_current_week": week_start == today,
        "week_range_label": format_week_range_label(week_start),
        "success_message": success_message,
        "error_message": error_message,
    }
    if is_connected:
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
async def calendar_index(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
    week_start: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    """Página del calendario: panel de eventos de la semana del usuario."""
    ctx = await _events_ctx(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        week_start=_resolve_week_start(week_start),
    )
    return render(request, full="pages/calendar/index.html", ctx=ctx)


@router.get("/events")
async def calendar_events(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
    week_start: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    ctx = await _events_ctx(
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


@router.post("/events")
async def calendar_create_event(
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
        await calendar_service.create_calendar_event(db, tenant.id, user.id, payload)
        success_message = "Evento creado en Google Calendar."
    except AppError as exc:
        error_message = exc.message
    ctx = await _events_ctx(
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


@router.post("/events/{event_id}")
async def calendar_update_event(
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
    ctx = await _events_ctx(
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
