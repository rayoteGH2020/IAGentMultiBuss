"""CRUD de citas internas (Paso 30 Fase B)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_hours_validation import (
    validate_appointment_start_on_grid,
    validate_appointment_within_center_hours,
)
from app.core.datetime_display import display_today, resolve_display_timezone
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.permissions import membership_can_appointment
from app.core.scheduling_granularity import validate_datetime_granularity
from app.models.appointment import Appointment
from app.models.membership import Membership
from app.schemas.scheduling import (
    AppointmentCancel,
    AppointmentCreate,
    AppointmentRead,
    AppointmentStatus,
    AppointmentUpdate,
)
from app.services import (
    audit_service,
    business_hours_service,
    professional_service,
    service_catalog_service,
)
from app.services.appointment_slot_service import OccupiedBlock, _filter_overlaps

if TYPE_CHECKING:
    from app.services.audit_service import AuditRequestContext

ACTION_APPOINTMENT_CREATED = "scheduling.appointment_created"
ACTION_APPOINTMENT_UPDATED = "scheduling.appointment_updated"
ACTION_APPOINTMENT_CANCELLED = "scheduling.appointment_cancelled"
ACTION_APPOINTMENT_STATUS_UPDATED = "scheduling.appointment_status_updated"
RESOURCE_APPOINTMENT = "appointment"


async def _tenant_now(db: AsyncSession, tenant_id: UUID) -> datetime:
    settings = await business_hours_service.get_scheduling_settings(db, tenant_id)
    tz = resolve_display_timezone(settings.timezone)
    return datetime.now(tz)


def _is_past_day(start_at: datetime, tenant_tz_name: str) -> bool:
    local = start_at.astimezone(resolve_display_timezone(tenant_tz_name))
    return local.date() < display_today(tenant_tz_name)


def _is_today_or_future(start_at: datetime, tenant_tz_name: str) -> bool:
    local = start_at.astimezone(resolve_display_timezone(tenant_tz_name))
    return local.date() >= display_today(tenant_tz_name)


def _align_to_granularity(dt: datetime, granularity_minutes: int) -> None:
    validate_datetime_granularity(dt, granularity_minutes)


async def _resolve_duration_minutes(
    db: AsyncSession,
    tenant_id: UUID,
    service_id: UUID | None,
    duration_minutes: int | None,
) -> int:
    if service_id is not None:
        service = await service_catalog_service.get_service(db, tenant_id, service_id)
        if not service.is_active:
            raise ValidationError("Selected service is inactive")
        return service.duration_minutes
    if duration_minutes is None:
        raise ValidationError("duration_minutes is required when service_id is not set")
    return duration_minutes


async def _validate_professional(
    db: AsyncSession,
    tenant_id: UUID,
    professional_id: UUID | None,
) -> None:
    if professional_id is None:
        return
    prof = await professional_service.get_professional(db, tenant_id, professional_id)
    if not prof.is_active:
        raise ValidationError("Professional is inactive and cannot be assigned")


async def _load_occupied_blocks(
    db: AsyncSession,
    tenant_id: UUID,
    professional_id: UUID,
    now: datetime,
    *,
    exclude_appointment_id: UUID | None = None,
) -> list[OccupiedBlock]:
    result = await db.execute(
        select(Appointment).where(
            Appointment.tenant_id == tenant_id,
            Appointment.professional_id == professional_id,
            Appointment.status != AppointmentStatus.cancelled,
            Appointment.start_at >= now.astimezone(UTC),
        )
    )
    blocks: list[OccupiedBlock] = []
    for row in result.scalars().all():
        if exclude_appointment_id is not None and row.id == exclude_appointment_id:
            continue
        blocks.append(
            OccupiedBlock(
                professional_id=professional_id,
                start_at=row.start_at,
                end_at=row.end_at,
            )
        )
    return blocks


async def _validate_overlap(
    db: AsyncSession,
    tenant_id: UUID,
    professional_id: UUID,
    start_at: datetime,
    end_at: datetime,
    buffer_minutes: int,
    now: datetime,
    *,
    exclude_appointment_id: UUID | None = None,
) -> None:
    occupied = await _load_occupied_blocks(
        db,
        tenant_id,
        professional_id,
        now,
        exclude_appointment_id=exclude_appointment_id,
    )
    from app.services.appointment_slot_service import SlotCandidate

    candidate = SlotCandidate(
        professional_id=professional_id, professional_name="", start_at=start_at
    )
    duration = int((end_at - start_at).total_seconds() // 60)
    free = _filter_overlaps([candidate], duration, buffer_minutes, occupied)
    if not free:
        raise ValidationError("Appointment overlaps with an existing booking (including buffer)")


async def _validate_center_business_hours(
    db: AsyncSession,
    tenant_id: UUID,
    start_at: datetime,
    end_at: datetime,
    timezone_name: str,
    granularity_minutes: int,
) -> None:
    business_hours = await business_hours_service.get_business_hours(db, tenant_id)
    local_start = start_at.astimezone(resolve_display_timezone(timezone_name))
    local_day = local_start.date()
    closed_dates = await business_hours_service.get_closed_dates(
        db,
        tenant_id,
        local_day,
        local_day,
    )
    validate_appointment_start_on_grid(
        local_day,
        local_start.time().replace(second=0, microsecond=0),
        business_hours,
        granularity_minutes,
    )
    validate_appointment_within_center_hours(
        start_at,
        end_at,
        business_hours,
        timezone_name=timezone_name,
        closed_dates=closed_dates,
    )


async def _validate_appointment_payload(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    service_id: UUID | None,
    professional_id: UUID | None,
    start_at: datetime,
    duration_minutes: int | None,
    client_name: str,
    client_phone: str,
    exclude_appointment_id: UUID | None = None,
    allow_past_today: bool = False,
) -> tuple[datetime, datetime, int]:
    settings = await business_hours_service.get_scheduling_settings(db, tenant_id)
    now = await _tenant_now(db, tenant_id)

    if start_at.tzinfo is None:
        raise ValidationError("start_at must be timezone-aware")

    granularity = settings.slot_granularity_minutes
    _align_to_granularity(start_at, granularity)

    if _is_past_day(start_at, settings.timezone):
        raise ValidationError("Cannot schedule appointments on past dates")

    if not allow_past_today and start_at < now:
        raise ValidationError("Cannot schedule appointments in the past")

    duration = await _resolve_duration_minutes(db, tenant_id, service_id, duration_minutes)
    end_at = start_at + timedelta(minutes=duration)
    _align_to_granularity(end_at, granularity)

    await _validate_center_business_hours(
        db,
        tenant_id,
        start_at,
        end_at,
        settings.timezone,
        granularity,
    )

    if not client_name.strip():
        raise ValidationError("client_name is required")
    if not client_phone.strip():
        raise ValidationError("client_phone is required")

    await _validate_professional(db, tenant_id, professional_id)

    if professional_id is not None:
        await _validate_overlap(
            db,
            tenant_id,
            professional_id,
            start_at,
            end_at,
            settings.buffer_minutes,
            now,
            exclude_appointment_id=exclude_appointment_id,
        )

    return start_at, end_at, duration


async def create_appointment(
    db: AsyncSession,
    tenant_id: UUID,
    payload: AppointmentCreate,
    *,
    created_by_user_id: UUID | None = None,
    request_ctx: AuditRequestContext | None = None,
) -> AppointmentRead:
    start_at, end_at, _duration = await _validate_appointment_payload(
        db,
        tenant_id,
        service_id=payload.service_id,
        professional_id=payload.professional_id,
        start_at=payload.start_at,
        duration_minutes=payload.duration_minutes,
        client_name=payload.client_name,
        client_phone=payload.client_phone,
    )
    appointment = Appointment(
        tenant_id=tenant_id,
        service_id=payload.service_id,
        professional_id=payload.professional_id,
        start_at=start_at,
        end_at=end_at,
        status=AppointmentStatus.scheduled,
        client_name=payload.client_name.strip(),
        client_phone=payload.client_phone.strip(),
        client_email=payload.client_email,
        notes=payload.notes,
        source="manual",
        created_by_user_id=created_by_user_id,
    )
    db.add(appointment)
    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=created_by_user_id,
        action=ACTION_APPOINTMENT_CREATED,
        resource_type=RESOURCE_APPOINTMENT,
        resource_id=appointment.id,
        metadata={"client_name": appointment.client_name, "start_at": start_at.isoformat()},
        request_ctx=request_ctx,
    )
    return AppointmentRead.model_validate(appointment)


async def get_appointment(
    db: AsyncSession,
    tenant_id: UUID,
    appointment_id: UUID,
) -> Appointment:
    result = await db.execute(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.tenant_id == tenant_id,
        )
    )
    appointment = result.scalar_one_or_none()
    if appointment is None:
        raise NotFoundError("Appointment not found")
    return appointment


async def update_appointment(
    db: AsyncSession,
    tenant_id: UUID,
    appointment_id: UUID,
    payload: AppointmentUpdate,
    *,
    actor_user_id: UUID | None = None,
    request_ctx: AuditRequestContext | None = None,
) -> AppointmentRead:
    appointment = await get_appointment(db, tenant_id, appointment_id)
    settings = await business_hours_service.get_scheduling_settings(db, tenant_id)

    if appointment.status == AppointmentStatus.cancelled:
        raise ValidationError("Cancelled appointments cannot be edited")

    if _is_past_day(appointment.start_at, settings.timezone):
        raise ValidationError("Past-day appointments are read-only")

    start_at, end_at, _duration = await _validate_appointment_payload(
        db,
        tenant_id,
        service_id=payload.service_id,
        professional_id=payload.professional_id,
        start_at=payload.start_at,
        duration_minutes=payload.duration_minutes,
        client_name=payload.client_name,
        client_phone=payload.client_phone,
        exclude_appointment_id=appointment_id,
        allow_past_today=True,
    )

    appointment.service_id = payload.service_id
    appointment.professional_id = payload.professional_id
    appointment.start_at = start_at
    appointment.end_at = end_at
    appointment.client_name = payload.client_name.strip()
    appointment.client_phone = payload.client_phone.strip()
    appointment.client_email = payload.client_email
    appointment.notes = payload.notes

    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=actor_user_id,
        action=ACTION_APPOINTMENT_UPDATED,
        resource_type=RESOURCE_APPOINTMENT,
        resource_id=appointment.id,
        metadata={"start_at": start_at.isoformat()},
        request_ctx=request_ctx,
    )
    return AppointmentRead.model_validate(appointment)


async def cancel_appointment(
    db: AsyncSession,
    tenant_id: UUID,
    appointment_id: UUID,
    payload: AppointmentCancel,
    *,
    actor_user_id: UUID | None = None,
    request_ctx: AuditRequestContext | None = None,
) -> AppointmentRead:
    appointment = await get_appointment(db, tenant_id, appointment_id)
    settings = await business_hours_service.get_scheduling_settings(db, tenant_id)

    if appointment.status == AppointmentStatus.cancelled:
        raise ValidationError("Appointment is already cancelled")

    if _is_past_day(appointment.start_at, settings.timezone):
        raise ValidationError("Past-day appointments cannot be cancelled")

    appointment.status = AppointmentStatus.cancelled
    appointment.cancelled_at = datetime.now(UTC)
    appointment.cancellation_reason = payload.cancellation_reason
    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=actor_user_id,
        action=ACTION_APPOINTMENT_CANCELLED,
        resource_type=RESOURCE_APPOINTMENT,
        resource_id=appointment.id,
        request_ctx=request_ctx,
    )
    return AppointmentRead.model_validate(appointment)


async def update_appointment_status(
    db: AsyncSession,
    tenant_id: UUID,
    appointment_id: UUID,
    new_status: AppointmentStatus,
    membership: Membership,
    *,
    actor_user_id: UUID | None = None,
    request_ctx: AuditRequestContext | None = None,
) -> AppointmentRead:
    appointment = await get_appointment(db, tenant_id, appointment_id)

    if new_status == AppointmentStatus.cancelled:
        raise ValidationError("Use cancel_appointment for cancellation")

    if new_status == AppointmentStatus.confirmed:
        if not (
            membership.role == "admin"
            or membership_can_appointment(membership, "create")
            or membership_can_appointment(membership, "edit")
        ):
            raise ForbiddenError("Requires create or edit permission to confirm")
    else:
        if membership.role != "admin" and not membership_can_appointment(membership, "view"):
            raise ForbiddenError("Requires view permission to change status")

    appointment.status = new_status
    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=actor_user_id,
        action=ACTION_APPOINTMENT_STATUS_UPDATED,
        resource_type=RESOURCE_APPOINTMENT,
        resource_id=appointment.id,
        metadata={"status": new_status.value},
        request_ctx=request_ctx,
    )
    return AppointmentRead.model_validate(appointment)


async def list_appointments(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    range_start: datetime,
    range_end: datetime,
    professional_id: UUID | None = None,
) -> list[AppointmentRead]:
    query = select(Appointment).where(
        Appointment.tenant_id == tenant_id,
        Appointment.start_at >= range_start,
        Appointment.start_at <= range_end,
    )
    if professional_id is not None:
        query = query.where(Appointment.professional_id == professional_id)
    query = query.order_by(Appointment.start_at)
    result = await db.execute(query)
    return [AppointmentRead.model_validate(row) for row in result.scalars().all()]
