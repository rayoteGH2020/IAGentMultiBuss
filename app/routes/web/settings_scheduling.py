"""Rutas web de scheduling en Settings (Paso 30 Fase C)."""

from __future__ import annotations

from datetime import date
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ValidationError, public_error_message
from app.core.professional_hours_grid import (
    allowed_center_slot_keys,
    build_center_period_slot_grids,
    build_professional_hours_grid_context,
    parse_working_slots_form,
)
from app.core.scheduling_form_parsers import parse_business_hours_form
from app.core.scheduling_granularity import DEFAULT_SLOT_GRANULARITY_MINUTES
from app.core.scheduling_ui import WEEKDAY_LABELS
from app.core.templating import render
from app.deps import CurrentTenant, CurrentUser, RequireAdmin, get_db
from app.schemas.membership import TenantMemberCreate, TenantMemberUpdate
from app.schemas.scheduling import (
    ProfessionalCreate,
    ProfessionalUpdate,
    ProfessionalWorkingHourRead,
    ReassignAppointmentsRequest,
    ScheduleExceptionCreate,
    SchedulingServiceCreate,
    SchedulingServiceUpdate,
    TenantSchedulingSettingsUpdate,
)
from app.services import (
    business_hours_service,
    membership_service,
    professional_service,
    service_catalog_service,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/settings", tags=["settings-scheduling"])


async def _professional_form_ctx(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    professional: object | None,
) -> dict[str, object]:
    services = await service_catalog_service.list_services(db, tenant_id)
    members = await membership_service.list_tenant_members(db, tenant_id)
    business_hours = await business_hours_service.get_business_hours(db, tenant_id)
    scheduling_settings = await business_hours_service.get_scheduling_settings(db, tenant_id)
    working_hours: list[ProfessionalWorkingHourRead] = []
    if professional is not None:
        working_hours = await professional_service.get_working_hours(
            db,
            tenant_id,
            professional.id,  # type: ignore[attr-defined]
        )
    hours_grid = build_professional_hours_grid_context(
        business_hours,
        working_hours,
        scheduling_settings.slot_granularity_minutes,
    )
    return {
        "professional": professional,
        "services": services,
        "members": members,
        "working_hours": working_hours,
        "weekday_labels": WEEKDAY_LABELS,
        "scheduling_settings": scheduling_settings,
        "professional_hours_grid": hours_grid,
    }


async def _scheduling_settings_ctx(db: AsyncSession, tenant_id: UUID) -> dict[str, object]:
    return {
        "business_hours": await business_hours_service.get_business_hours(db, tenant_id),
        "scheduling_settings": await business_hours_service.get_scheduling_settings(db, tenant_id),
        "exceptions": await business_hours_service.list_schedule_exceptions(db, tenant_id),
        "weekday_labels": WEEKDAY_LABELS,
    }


@router.get("/business-hours")
async def business_hours_page(
    request: Request,
    _user: CurrentUser,
    tenant: CurrentTenant,
    _: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    ctx = await _scheduling_settings_ctx(db, tenant.id)
    return render(request, full="pages/settings/business_hours.html", ctx=ctx)


@router.post("/business-hours")
async def business_hours_save(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    _: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    timezone: str = Form("Europe/Madrid"),
    search_horizon_days: int = Form(14),
    slot_granularity_minutes: int = Form(DEFAULT_SLOT_GRANULARITY_MINUTES),
    buffer_minutes: int = Form(10),
) -> HTMLResponse:
    weekday_forms: list[tuple[int, int, str | None, str | None]] = []
    form = await request.form()
    for weekday in range(7):
        for sort_order in range(2):
            opens_key = f"weekday_{weekday}_sort_{sort_order}_opens"
            closes_key = f"weekday_{weekday}_sort_{sort_order}_closes"
            opens_raw = form.get(opens_key)
            closes_raw = form.get(closes_key)
            weekday_forms.append(
                (
                    weekday,
                    sort_order,
                    opens_raw if isinstance(opens_raw, str) else None,
                    closes_raw if isinstance(closes_raw, str) else None,
                )
            )
    try:
        await business_hours_service.replace_business_hours(
            db,
            tenant.id,
            parse_business_hours_form(
                weekday_forms=weekday_forms,
                weekday_labels=WEEKDAY_LABELS,
            ),
            user_id=user.id,
        )
        await business_hours_service.update_scheduling_settings(
            db,
            tenant.id,
            TenantSchedulingSettingsUpdate(
                timezone=timezone,
                search_horizon_days=search_horizon_days,
                slot_granularity_minutes=slot_granularity_minutes,
                buffer_minutes=buffer_minutes,
            ),
            user_id=user.id,
        )
        saved = True
        error_message = None
    except AppError as exc:
        saved = False
        error_message = public_error_message(
            exc,
            fallback="No se pudo guardar la configuración de horarios.",
        )
        logger.warning("settings.business_hours_save_failed", error=exc.message)
    except PydanticValidationError as exc:
        saved = False
        errors = exc.errors()
        detail = (
            str(errors[0].get("msg", "Datos de configuración inválidos"))
            if errors
            else "Datos de configuración inválidos"
        )
        error_message = public_error_message(
            ValidationError(detail),
            fallback="No se pudo guardar la configuración de horarios.",
        )
        logger.warning("settings.business_hours_save_failed", error=detail)

    ctx = await _scheduling_settings_ctx(db, tenant.id)
    ctx["saved"] = saved
    ctx["error_message"] = error_message
    return render(
        request,
        full="pages/settings/business_hours.html",
        partial="components/scheduling/business_hours_panel.html",
        ctx=ctx,
    )


@router.post("/business-hours/exceptions")
async def schedule_exception_create(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    _: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    exception_date: date = Form(...),
    label: str | None = Form(None),
) -> HTMLResponse:
    row = await business_hours_service.create_schedule_exception(
        db,
        tenant.id,
        ScheduleExceptionCreate(exception_date=exception_date, label=label or None),
        user_id=user.id,
    )
    return render(
        request,
        full="components/scheduling/exception_row.html",
        partial="components/scheduling/exception_row.html",
        ctx={"exception": row},
    )


@router.delete("/business-hours/exceptions/{exception_id}")
async def schedule_exception_delete(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    _: RequireAdmin,
    exception_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    await business_hours_service.delete_schedule_exception(
        db,
        tenant.id,
        exception_id,
        user_id=user.id,
    )
    return HTMLResponse(content="", status_code=200)


@router.get("/professionals")
async def professionals_page(
    request: Request,
    _user: CurrentUser,
    tenant: CurrentTenant,
    _: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    professionals = await professional_service.list_professionals(db, tenant.id)
    return render(
        request,
        full="pages/settings/professionals.html",
        ctx={"professionals": professionals},
    )


@router.get("/professionals/new")
async def professional_new_form(
    request: Request,
    tenant: CurrentTenant,
    _: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    return render(
        request,
        full="components/scheduling/professional_form.html",
        partial="components/scheduling/professional_form.html",
        ctx=await _professional_form_ctx(db, tenant.id, professional=None),
    )


@router.get("/professionals/{professional_id}/edit")
async def professional_edit_form(
    request: Request,
    tenant: CurrentTenant,
    _: RequireAdmin,
    professional_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    prof = await professional_service.get_professional(db, tenant.id, professional_id)
    return render(
        request,
        full="components/scheduling/professional_form.html",
        partial="components/scheduling/professional_form.html",
        ctx=await _professional_form_ctx(db, tenant.id, professional=prof),
    )


@router.post("/professionals")
async def professional_create(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    _: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    display_name: str = Form(...),
    color: str = Form("#6366f1"),
    is_bookable: bool = Form(True),
    user_id: str | None = Form(None),
) -> HTMLResponse:
    form = await request.form()
    specialty_raw = form.getlist("specialty_service_ids")
    specialty_ids = [UUID(v) for v in specialty_raw if isinstance(v, str) and v]
    linked_user = UUID(user_id) if user_id else None
    prof = await professional_service.create_professional(
        db,
        tenant.id,
        ProfessionalCreate(
            display_name=display_name,
            color=color,
            is_bookable=is_bookable,
            user_id=linked_user,
            specialty_service_ids=specialty_ids,
        ),
        user_id=user.id,
    )
    return render(
        request,
        full="components/scheduling/professional_row.html",
        partial="components/scheduling/professional_row.html",
        ctx={"professional": prof},
    )


@router.post("/professionals/{professional_id}")
async def professional_update(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    _: RequireAdmin,
    professional_id: UUID,
    db: AsyncSession = Depends(get_db),
    display_name: str = Form(...),
    color: str = Form("#6366f1"),
    is_active: str | None = Form(None),
    is_bookable: str | None = Form(None),
    user_id: str | None = Form(None),
) -> HTMLResponse:
    form = await request.form()
    specialty_raw = form.getlist("specialty_service_ids")
    specialty_ids = [UUID(v) for v in specialty_raw if isinstance(v, str) and v]
    linked_user = UUID(user_id) if user_id else None
    active_flag = is_active in ("true", "on", "1")
    bookable_flag = is_bookable in ("true", "on", "1")
    try:
        prof = await professional_service.update_professional(
            db,
            tenant.id,
            professional_id,
            ProfessionalUpdate(
                display_name=display_name,
                color=color,
                is_active=active_flag,
                is_bookable=bookable_flag,
                user_id=linked_user,
                specialty_service_ids=specialty_ids,
            ),
            user_id=user.id,
        )
        business_hours = await business_hours_service.get_business_hours(db, tenant.id)
        scheduling_settings = await business_hours_service.get_scheduling_settings(db, tenant.id)
        period_grids = build_center_period_slot_grids(
            business_hours,
            scheduling_settings.slot_granularity_minutes,
        )
        allowed = allowed_center_slot_keys(period_grids)
        slot_values = [str(v) for v in form.getlist("working_slots")]
        hours_payload = parse_working_slots_form(
            slot_values,
            allowed_keys=allowed,
            granularity_minutes=scheduling_settings.slot_granularity_minutes,
        )
        await professional_service.replace_working_hours(
            db,
            tenant.id,
            professional_id,
            hours_payload,
            user_id=user.id,
        )
    except ValidationError as exc:
        if exc.details.get("future_appointment_count"):
            return render(
                request,
                full="components/scheduling/professional_reassign.html",
                partial="components/scheduling/professional_reassign.html",
                ctx={
                    "professional_id": professional_id,
                    "future_count": exc.details["future_appointment_count"],
                    "professionals": await professional_service.list_professionals(db, tenant.id),
                },
            )
        orm_prof = await professional_service.get_professional(db, tenant.id, professional_id)
        ctx = await _professional_form_ctx(db, tenant.id, professional=orm_prof)
        ctx["hours_error_message"] = public_error_message(
            exc,
            fallback="No se pudo guardar el horario del profesional.",
        )
        return render(
            request,
            full="components/scheduling/professional_form.html",
            partial="components/scheduling/professional_form.html",
            ctx=ctx,
        )
    return render(
        request,
        full="components/scheduling/professional_row.html",
        partial="components/scheduling/professional_row.html",
        ctx={"professional": prof},
    )


@router.get("/professionals/{professional_id}/reassign")
async def professional_reassign_form(
    request: Request,
    tenant: CurrentTenant,
    _: RequireAdmin,
    professional_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    count = await professional_service.count_future_appointments(db, tenant.id, professional_id)
    return render(
        request,
        full="components/scheduling/professional_reassign.html",
        partial="components/scheduling/professional_reassign.html",
        ctx={
            "professional_id": professional_id,
            "future_count": count,
            "professionals": await professional_service.list_professionals(db, tenant.id),
        },
    )


@router.post("/professionals/{professional_id}/reassign")
async def professional_reassign_submit(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    _: RequireAdmin,
    professional_id: UUID,
    db: AsyncSession = Depends(get_db),
    target_professional_id: UUID = Form(...),
) -> HTMLResponse:
    appointment_ids = await professional_service.list_future_appointment_ids(
        db, tenant.id, professional_id
    )
    if appointment_ids:
        await professional_service.reassign_future_appointments(
            db,
            tenant.id,
            professional_id,
            ReassignAppointmentsRequest(
                target_professional_id=target_professional_id,
                appointment_ids=appointment_ids,
            ),
            user_id=user.id,
        )
    prof = await professional_service.deactivate_professional(
        db,
        tenant.id,
        professional_id,
        user_id=user.id,
    )
    return render(
        request,
        full="components/scheduling/professional_row.html",
        partial="components/scheduling/professional_row.html",
        ctx={"professional": prof},
    )


@router.get("/services")
async def services_page(
    request: Request,
    _user: CurrentUser,
    tenant: CurrentTenant,
    _: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    services = await service_catalog_service.list_services(db, tenant.id)
    return render(request, full="pages/settings/services.html", ctx={"services": services})


@router.post("/services")
async def service_create(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    _: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    duration_minutes: int = Form(...),
) -> HTMLResponse:
    row = await service_catalog_service.create_service(
        db,
        tenant.id,
        SchedulingServiceCreate(name=name, duration_minutes=duration_minutes),
        user_id=user.id,
    )
    return render(
        request,
        full="components/scheduling/service_row.html",
        partial="components/scheduling/service_row.html",
        ctx={"service": row},
    )


@router.post("/services/{service_id}")
async def service_update(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    _: RequireAdmin,
    service_id: UUID,
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    duration_minutes: int = Form(...),
    is_active: str | None = Form(None),
) -> HTMLResponse:
    active_flag = is_active in ("true", "on", "1")
    row = await service_catalog_service.update_service(
        db,
        tenant.id,
        service_id,
        SchedulingServiceUpdate(
            name=name,
            duration_minutes=duration_minutes,
            is_active=active_flag,
        ),
        user_id=user.id,
    )
    return render(
        request,
        full="components/scheduling/service_row.html",
        partial="components/scheduling/service_row.html",
        ctx={"service": row},
    )


@router.get("/members")
async def members_page(
    request: Request,
    _user: CurrentUser,
    tenant: CurrentTenant,
    _: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    members = await membership_service.list_tenant_members(db, tenant.id)
    return render(request, full="pages/settings/members.html", ctx={"members": members})


@router.get("/members/new")
async def member_new_form(
    request: Request,
    _: RequireAdmin,
) -> HTMLResponse:
    return render(
        request,
        full="components/scheduling/member_form.html",
        partial="components/scheduling/member_form.html",
        ctx={"member": None},
    )


@router.get("/members/{membership_id}/edit")
async def member_edit_form(
    request: Request,
    tenant: CurrentTenant,
    _: RequireAdmin,
    membership_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    members = await membership_service.list_tenant_members(db, tenant.id)
    member = next((m for m in members if m.membership_id == membership_id), None)
    if member is None:
        return RedirectResponse("/settings/members", status_code=302)
    return render(
        request,
        full="components/scheduling/member_form.html",
        partial="components/scheduling/member_form.html",
        ctx={"member": member},
    )


@router.post("/members")
async def member_create(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    _: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    email: str = Form(...),
    name: str = Form(...),
    role: str = Form("member"),
    perm_view: bool = Form(True),
    perm_create: bool = Form(False),
    perm_edit: bool = Form(False),
    perm_cancel: bool = Form(False),
) -> HTMLResponse:
    from app.schemas.membership import AppointmentPermissions, MembershipPermissions

    row = await membership_service.create_tenant_member(
        db,
        tenant.id,
        TenantMemberCreate(
            email=email,
            name=name,
            role=role,  # type: ignore[arg-type]
            permissions=MembershipPermissions(
                appointments=AppointmentPermissions(
                    view=perm_view,
                    create=perm_create,
                    edit=perm_edit,
                    cancel=perm_cancel,
                )
            ),
        ),
        actor_user_id=user.id,
    )
    return render(
        request,
        full="components/scheduling/member_row.html",
        partial="components/scheduling/member_row.html",
        ctx={"member": row},
    )


@router.post("/members/{membership_id}")
async def member_update(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    _: RequireAdmin,
    membership_id: UUID,
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    role: str = Form("member"),
    perm_view: bool = Form(True),
    perm_create: bool = Form(False),
    perm_edit: bool = Form(False),
    perm_cancel: bool = Form(False),
) -> HTMLResponse:
    from app.schemas.membership import AppointmentPermissions, MembershipPermissions

    row = await membership_service.update_tenant_member(
        db,
        tenant.id,
        membership_id,
        TenantMemberUpdate(
            name=name,
            role=role,  # type: ignore[arg-type]
            permissions=MembershipPermissions(
                appointments=AppointmentPermissions(
                    view=perm_view,
                    create=perm_create,
                    edit=perm_edit,
                    cancel=perm_cancel,
                )
            ),
        ),
        actor_user_id=user.id,
    )
    return render(
        request,
        full="components/scheduling/member_row.html",
        partial="components/scheduling/member_row.html",
        ctx={"member": row},
    )


@router.delete("/members/{membership_id}")
async def member_delete(
    user: CurrentUser,
    tenant: CurrentTenant,
    _: RequireAdmin,
    membership_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    await membership_service.remove_tenant_member(
        db,
        tenant.id,
        membership_id,
        actor_user_id=user.id,
    )
    return HTMLResponse(content="", status_code=200)
