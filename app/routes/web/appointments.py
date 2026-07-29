"""Rutas web del calendario interno de citas (Paso 30 Fase C)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.core.business_hours_validation import (
    build_grid_times_by_weekday,
    parse_appointment_form_start,
)
from app.core.datetime_display import resolve_display_timezone
from app.core.errors import AppError, public_error_message
from app.core.permissions import membership_can_appointment
from app.core.scheduling_ui import (
    DayAppointmentBlock,
    build_day_calendar_grid,
    build_month_calendar_view,
    build_week_calendar_view,
    calendar_hour_slots,
    compute_date_range,
    format_range_label,
    group_blocks_by_professional,
    is_appointment_read_only,
    layout_day_appointment_blocks,
    parse_anchor_date,
    range_to_datetimes,
    shift_anchor,
    sort_professionals_for_service,
)
from app.core.templating import render
from app.deps import (
    CurrentMembership,
    CurrentTenant,
    CurrentUser,
    RequireAppointmentCancel,
    RequireAppointmentCreate,
    RequireAppointmentEdit,
    RequireAppointmentView,
    get_db,
    require_appointment_permission,
)
from app.schemas.scheduling import (
    AppointmentCancel,
    AppointmentCreate,
    AppointmentStatus,
    AppointmentUpdate,
    FindSlotsRequest,
    sanitize_professional_color,
)
from app.services import (
    appointment_slot_service,
    business_hours_service,
    internal_appointment_service,
    professional_service,
    service_catalog_service,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/appointments", tags=["appointments"])

DEFAULT_APPOINTMENTS_VIEW = "day"


def _can(membership: object, action: str) -> bool:
    role = getattr(membership, "role", None)
    if role == "admin":
        return True
    if not hasattr(membership, "permissions"):
        return False
    return membership_can_appointment(membership, action)  # type: ignore[arg-type]


async def _calendar_ctx(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    view: str,
    anchor: str | None,
    membership: object,
) -> dict[str, object]:
    settings = await business_hours_service.get_scheduling_settings(db, tenant_id)
    anchor_date = parse_anchor_date(anchor, settings.timezone)
    if view not in ("day", "week", "month"):
        view = DEFAULT_APPOINTMENTS_VIEW
    range_start, range_end = compute_date_range(view, anchor_date)
    dt_start, dt_end = range_to_datetimes(range_start, range_end, settings.timezone)

    appointments = await internal_appointment_service.list_appointments(
        db,
        tenant_id,
        range_start=dt_start,
        range_end=dt_end,
    )

    professionals = await professional_service.list_professionals(db, tenant_id)

    month_days: list[date] = []
    if view == "month":
        cursor = range_start
        while cursor <= range_end:
            month_days.append(cursor)
            cursor += timedelta(days=1)

    business_hours = await business_hours_service.get_business_hours(db, tenant_id)
    closed_dates = await business_hours_service.get_closed_dates(
        db,
        tenant_id,
        range_start,
        range_end,
    )
    prof_colors = {p.id: sanitize_professional_color(p.color) for p in professionals}

    today = parse_anchor_date(None, settings.timezone)
    day_grid = None
    week_view = None
    month_view = None
    appointment_blocks_by_prof: dict[str, list[DayAppointmentBlock]] = {}

    if view == "day":
        day_grid = build_day_calendar_grid(
            business_hours,
            anchor_date.weekday(),
            granularity_minutes=settings.slot_granularity_minutes,
            is_closed_day=anchor_date in closed_dates,
        )
        blocks = layout_day_appointment_blocks(
            appointments,
            day_grid,
            anchor_date,
            settings.timezone,
            professional_colors=prof_colors,
        )
        appointment_blocks_by_prof = group_blocks_by_professional(blocks)
    elif view == "week":
        week_view = build_week_calendar_view(
            business_hours,
            range_start,
            closed_dates=closed_dates,
            appointments=appointments,
            timezone=settings.timezone,
            granularity_minutes=settings.slot_granularity_minutes,
            today=today,
            professional_colors=prof_colors,
        )
    elif view == "month":
        month_view = build_month_calendar_view(
            business_hours,
            range_start,
            range_end,
            closed_dates=closed_dates,
            appointments=appointments,
            timezone=settings.timezone,
            granularity_minutes=settings.slot_granularity_minutes,
            today=today,
            professional_colors=prof_colors,
        )

    return {
        "view": view,
        "anchor_date": anchor_date.isoformat(),
        "range_start": range_start,
        "range_end": range_end,
        "range_label": format_range_label(
            view,
            range_start,
            range_end,
            timezone=settings.timezone,
        ),
        "is_today": view == "day" and anchor_date == today,
        "prev_anchor": shift_anchor(view, anchor_date, -1).isoformat(),
        "next_anchor": shift_anchor(view, anchor_date, 1).isoformat(),
        "today_anchor": today.isoformat(),
        "appointments": appointments,
        "professionals": professionals,
        "month_days": month_days,
        "hour_slots": calendar_hour_slots(),
        "scheduling_timezone": settings.timezone,
        "scheduling_settings": settings,
        "day_grid": day_grid,
        "week_view": week_view,
        "month_view": month_view,
        "appointment_blocks_by_prof": appointment_blocks_by_prof,
        "can_create": _can(membership, "create"),
        "can_edit": _can(membership, "edit"),
        "can_cancel": _can(membership, "cancel"),
    }


async def _appointment_form_ctx(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    appointment: object | None,
    mode: str,
) -> dict[str, object]:
    services = await service_catalog_service.list_services(db, tenant_id)
    professionals = await professional_service.list_professionals(db, tenant_id)
    service_id = getattr(appointment, "service_id", None)
    ordered_profs = sort_professionals_for_service(professionals, service_id)
    settings = await business_hours_service.get_scheduling_settings(db, tenant_id)
    business_hours = await business_hours_service.get_business_hours(db, tenant_id)
    grid_times_by_weekday = build_grid_times_by_weekday(
        business_hours,
        settings.slot_granularity_minutes,
    )
    today = parse_anchor_date(None, settings.timezone)
    closed_dates = await business_hours_service.get_closed_dates(
        db,
        tenant_id,
        today,
        today + timedelta(days=settings.search_horizon_days),
    )

    default_date = today.isoformat()
    default_hour = ""
    default_minute = ""
    appointment_duration_minutes: int | None = None
    if appointment is not None:
        start_at = getattr(appointment, "start_at", None)
        end_at = getattr(appointment, "end_at", None)
        if isinstance(start_at, datetime) and isinstance(end_at, datetime):
            local_start = start_at.astimezone(resolve_display_timezone(settings.timezone))
            default_date = local_start.date().isoformat()
            default_hour = local_start.strftime("%H")
            default_minute = local_start.strftime("%M")
            appointment_duration_minutes = int((end_at - start_at).total_seconds() // 60)

    return {
        "appointment": appointment,
        "mode": mode,
        "services": services,
        "professionals_ordered": ordered_profs,
        "statuses": [s for s in AppointmentStatus if s != AppointmentStatus.cancelled],
        "scheduling_timezone": settings.timezone,
        "scheduling_settings": settings,
        "grid_times_by_weekday": grid_times_by_weekday,
        "closed_dates": sorted(closed.isoformat() for closed in closed_dates),
        "appointment_default_date": default_date,
        "appointment_default_hour": default_hour,
        "appointment_default_minute": default_minute,
        "appointment_duration_minutes": appointment_duration_minutes,
    }


def _resolve_appointment_start_from_form(
    *,
    appointment_date: date,
    start_time: str,
    timezone_name: str,
) -> datetime:
    return parse_appointment_form_start(appointment_date, start_time, timezone_name)


@router.get("")
async def appointments_index(
    request: Request,
    _user: CurrentUser,
    tenant: CurrentTenant,
    membership: RequireAppointmentView,
    db: AsyncSession = Depends(get_db),
    view: str | None = None,
    date: str | None = None,
) -> Response:
    if view is None and date is None:
        settings = await business_hours_service.get_scheduling_settings(db, tenant.id)
        today = parse_anchor_date(None, settings.timezone).isoformat()
        return RedirectResponse(
            url=f"/appointments?view={DEFAULT_APPOINTMENTS_VIEW}&date={today}",
            status_code=302,
        )

    ctx = await _calendar_ctx(
        db,
        tenant.id,
        view=view or DEFAULT_APPOINTMENTS_VIEW,
        anchor=date,
        membership=membership,
    )
    return render(request, full="pages/appointments/index.html", ctx=ctx)


@router.get("/calendar")
async def appointments_calendar_fragment(
    request: Request,
    tenant: CurrentTenant,
    membership: RequireAppointmentView,
    db: AsyncSession = Depends(get_db),
    view: str = Query(DEFAULT_APPOINTMENTS_VIEW),
    date: str | None = Query(None),
) -> HTMLResponse:
    ctx = await _calendar_ctx(db, tenant.id, view=view, anchor=date, membership=membership)
    return render(
        request,
        full="components/scheduling/calendar_grid.html",
        partial="components/scheduling/calendar_grid.html",
        ctx=ctx,
    )


@router.get("/find-slots")
async def appointment_find_slots(
    request: Request,
    tenant: CurrentTenant,
    _membership: Annotated[object, Depends(require_appointment_permission("create", "edit"))],
    db: AsyncSession = Depends(get_db),
    service_id: UUID = Query(...),
    after: str | None = Query(None),
    start_at: str | None = Query(None),
    appointment_date: date | None = Query(None),
    start_time: str | None = Query(None),
    professional_id: UUID | None = Query(None),
    mode: str = Query("create"),
) -> HTMLResponse:
    settings = await business_hours_service.get_scheduling_settings(db, tenant.id)
    if appointment_date is not None and start_time:
        after_dt = parse_appointment_form_start(
            appointment_date,
            start_time,
            settings.timezone,
        )
    else:
        raw_after = start_at or after
        if not raw_after:
            from app.core.errors import ValidationError

            raise ValidationError("after, start_at, or appointment_date+start_time is required")
        after_dt = datetime.fromisoformat(raw_after)
        if after_dt.tzinfo is None:
            after_dt = after_dt.replace(tzinfo=resolve_display_timezone(settings.timezone))

    try:
        result = await appointment_slot_service.find_next_available_slots(
            db,
            tenant.id,
            FindSlotsRequest(
                service_id=service_id,
                after=after_dt,
                professional_id=professional_id,
            ),
        )
        error_message = None
    except AppError as exc:
        result = None
        error_message = public_error_message(
            exc,
            fallback="No se pudieron calcular los huecos disponibles.",
        )

    return render(
        request,
        full="components/scheduling/slot_results.html",
        partial="components/scheduling/slot_results.html",
        ctx={"slots_response": result, "error_message": error_message, "mode": mode},
    )


@router.get("/new")
async def appointment_new_form(
    request: Request,
    tenant: CurrentTenant,
    _: RequireAppointmentCreate,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    ctx = await _appointment_form_ctx(db, tenant.id, appointment=None, mode="create")
    ctx["read_only"] = False
    return render(
        request,
        full="components/scheduling/appointment_form.html",
        partial="components/scheduling/appointment_form.html",
        ctx=ctx,
    )


@router.post("")
async def appointment_create(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    _: RequireAppointmentCreate,
    db: AsyncSession = Depends(get_db),
    service_id: str | None = Form(None),
    professional_id: str | None = Form(None),
    appointment_date: date = Form(...),
    start_time: str = Form(...),
    duration_minutes: int | None = Form(None),
    client_name: str = Form(...),
    client_phone: str = Form(...),
    client_email: str | None = Form(None),
    notes: str | None = Form(None),
) -> HTMLResponse:
    settings = await business_hours_service.get_scheduling_settings(db, tenant.id)
    start_dt = _resolve_appointment_start_from_form(
        appointment_date=appointment_date,
        start_time=start_time,
        timezone_name=settings.timezone,
    )

    row = await internal_appointment_service.create_appointment(
        db,
        tenant.id,
        AppointmentCreate(
            service_id=UUID(service_id) if service_id else None,
            professional_id=UUID(professional_id) if professional_id else None,
            start_at=start_dt,
            duration_minutes=duration_minutes,
            client_name=client_name,
            client_phone=client_phone,
            client_email=client_email or None,
            notes=notes or None,
        ),
        created_by_user_id=user.id,
    )
    response = render(
        request,
        full="components/scheduling/appointment_created.html",
        partial="components/scheduling/appointment_created.html",
        ctx={"appointment": row},
    )
    response.headers["HX-Trigger"] = "appointmentChanged"
    return response


@router.get("/{appointment_id}")
async def appointment_detail_form(
    request: Request,
    tenant: CurrentTenant,
    membership: RequireAppointmentView,
    appointment_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    appointment = await internal_appointment_service.get_appointment(db, tenant.id, appointment_id)
    settings = await business_hours_service.get_scheduling_settings(db, tenant.id)
    read_only = is_appointment_read_only(appointment.start_at, settings.timezone)
    can_edit = not read_only and _can(membership, "edit")
    mode = "view" if read_only or not can_edit else "edit"
    ctx = await _appointment_form_ctx(db, tenant.id, appointment=appointment, mode=mode)
    ctx["read_only"] = read_only
    return render(
        request,
        full="components/scheduling/appointment_form.html",
        partial="components/scheduling/appointment_form.html",
        ctx=ctx,
    )


@router.post("/{appointment_id}")
async def appointment_update(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    membership: CurrentMembership,
    _: RequireAppointmentEdit,
    appointment_id: UUID,
    db: AsyncSession = Depends(get_db),
    service_id: str | None = Form(None),
    professional_id: str | None = Form(None),
    appointment_date: date = Form(...),
    start_time: str = Form(...),
    duration_minutes: int | None = Form(None),
    client_name: str = Form(...),
    client_phone: str = Form(...),
    client_email: str | None = Form(None),
    notes: str | None = Form(None),
    status: str | None = Form(None),
) -> HTMLResponse:
    settings = await business_hours_service.get_scheduling_settings(db, tenant.id)
    start_dt = _resolve_appointment_start_from_form(
        appointment_date=appointment_date,
        start_time=start_time,
        timezone_name=settings.timezone,
    )

    row = await internal_appointment_service.update_appointment(
        db,
        tenant.id,
        appointment_id,
        AppointmentUpdate(
            service_id=UUID(service_id) if service_id else None,
            professional_id=UUID(professional_id) if professional_id else None,
            start_at=start_dt,
            duration_minutes=duration_minutes,
            client_name=client_name,
            client_phone=client_phone,
            client_email=client_email or None,
            notes=notes or None,
        ),
        actor_user_id=user.id,
    )
    if status and status != row.status.value:
        row = await internal_appointment_service.update_appointment_status(
            db,
            tenant.id,
            appointment_id,
            AppointmentStatus(status),
            membership,
            actor_user_id=user.id,
        )
    response = render(
        request,
        full="components/scheduling/appointment_created.html",
        partial="components/scheduling/appointment_created.html",
        ctx={"appointment": row, "updated": True},
    )
    response.headers["HX-Trigger"] = "appointmentChanged"
    return response


@router.post("/{appointment_id}/cancel")
async def appointment_cancel(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    _: RequireAppointmentCancel,
    appointment_id: UUID,
    db: AsyncSession = Depends(get_db),
    cancellation_reason: str | None = Form(None),
) -> HTMLResponse:
    await internal_appointment_service.cancel_appointment(
        db,
        tenant.id,
        appointment_id,
        AppointmentCancel(cancellation_reason=cancellation_reason or None),
        actor_user_id=user.id,
    )
    response = HTMLResponse(content="", status_code=200)
    response.headers["HX-Trigger"] = "appointmentChanged"
    return response
