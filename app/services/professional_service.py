"""CRUD de profesionales, horarios y especialidades (Paso 30 Fase B)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.datetime_display import resolve_display_timezone
from app.core.errors import NotFoundError, ValidationError
from app.models.appointment import Appointment
from app.models.business_hour import BusinessHour
from app.models.professional import Professional
from app.models.professional_specialty import ProfessionalSpecialty
from app.models.professional_working_hour import ProfessionalWorkingHour
from app.schemas.scheduling import (
    AppointmentStatus,
    ProfessionalCreate,
    ProfessionalRead,
    ProfessionalUpdate,
    ProfessionalWorkingHourRead,
    ProfessionalWorkingHoursUpdate,
    ReassignAppointmentsRequest,
)
from app.services import audit_service, business_hours_service

if TYPE_CHECKING:
    from app.services.audit_service import AuditRequestContext

ACTION_PROFESSIONAL_CREATED = "scheduling.professional_created"
ACTION_PROFESSIONAL_UPDATED = "scheduling.professional_updated"
ACTION_PROFESSIONAL_DEACTIVATED = "scheduling.professional_deactivated"
ACTION_APPOINTMENTS_REASSIGNED = "scheduling.appointments_reassigned"
RESOURCE_PROFESSIONAL = "professional"

MAX_SPECIALTIES = 3


async def list_professionals(db: AsyncSession, tenant_id: UUID) -> list[ProfessionalRead]:
    result = await db.execute(
        select(Professional)
        .where(Professional.tenant_id == tenant_id)
        .options(selectinload(Professional.specialties))
        .order_by(Professional.sort_order, Professional.display_name)
    )
    return [_to_read(prof) for prof in result.scalars().all()]


async def get_professional(
    db: AsyncSession,
    tenant_id: UUID,
    professional_id: UUID,
) -> Professional:
    result = await db.execute(
        select(Professional)
        .where(Professional.id == professional_id, Professional.tenant_id == tenant_id)
        .options(selectinload(Professional.specialties))
    )
    prof = result.scalar_one_or_none()
    if prof is None:
        raise NotFoundError("Professional not found")
    return prof


def _to_read(prof: Professional) -> ProfessionalRead:
    specialty_ids = sorted(prof.specialties, key=lambda s: s.sort_order)
    return ProfessionalRead(
        id=prof.id,
        display_name=prof.display_name,
        user_id=prof.user_id,
        color=prof.color,
        is_active=prof.is_active,
        is_bookable=prof.is_bookable,
        sort_order=prof.sort_order,
        specialty_service_ids=[s.service_id for s in specialty_ids],
    )


async def _copy_center_hours_as_default(
    db: AsyncSession,
    tenant_id: UUID,
    professional_id: UUID,
) -> None:
    result = await db.execute(select(BusinessHour).where(BusinessHour.tenant_id == tenant_id))
    for bh in result.scalars().all():
        db.add(
            ProfessionalWorkingHour(
                tenant_id=tenant_id,
                professional_id=professional_id,
                weekday=bh.weekday,
                sort_order=bh.sort_order,
                opens_at=bh.opens_at,
                closes_at=bh.closes_at,
            )
        )


async def _replace_specialties(
    db: AsyncSession,
    tenant_id: UUID,
    professional_id: UUID,
    service_ids: list[UUID],
) -> None:
    if len(service_ids) > MAX_SPECIALTIES:
        raise ValidationError(f"At most {MAX_SPECIALTIES} specialties allowed")
    if len(set(service_ids)) != len(service_ids):
        raise ValidationError("Duplicate specialty service ids")

    from app.services.service_catalog_service import get_service

    for service_id in service_ids:
        service = await get_service(db, tenant_id, service_id)
        if not service.is_active:
            raise ValidationError("Specialty services must be active")

    await db.execute(
        delete(ProfessionalSpecialty).where(
            ProfessionalSpecialty.professional_id == professional_id
        )
    )
    for idx, service_id in enumerate(service_ids):
        db.add(
            ProfessionalSpecialty(
                tenant_id=tenant_id,
                professional_id=professional_id,
                service_id=service_id,
                sort_order=idx,
            )
        )


async def create_professional(
    db: AsyncSession,
    tenant_id: UUID,
    payload: ProfessionalCreate,
    *,
    user_id: UUID | None = None,
    request_ctx: AuditRequestContext | None = None,
) -> ProfessionalRead:
    prof = Professional(
        tenant_id=tenant_id,
        display_name=payload.display_name,
        user_id=payload.user_id,
        color=payload.color,
        is_active=payload.is_active,
        is_bookable=payload.is_bookable,
        sort_order=payload.sort_order,
    )
    db.add(prof)
    await db.flush()
    await _copy_center_hours_as_default(db, tenant_id, prof.id)
    if payload.specialty_service_ids:
        await _replace_specialties(db, tenant_id, prof.id, payload.specialty_service_ids)
    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_PROFESSIONAL_CREATED,
        resource_type=RESOURCE_PROFESSIONAL,
        resource_id=prof.id,
        metadata={"display_name": payload.display_name},
        request_ctx=request_ctx,
    )
    return _to_read(await get_professional(db, tenant_id, prof.id))


async def update_professional(
    db: AsyncSession,
    tenant_id: UUID,
    professional_id: UUID,
    payload: ProfessionalUpdate,
    *,
    user_id: UUID | None = None,
    request_ctx: AuditRequestContext | None = None,
) -> ProfessionalRead:
    prof = await get_professional(db, tenant_id, professional_id)

    if payload.is_active is False and prof.is_active:
        await _ensure_no_future_appointments_or_raise(db, tenant_id, professional_id)

    if payload.display_name is not None:
        prof.display_name = payload.display_name
    if payload.user_id is not None:
        prof.user_id = payload.user_id
    if payload.color is not None:
        prof.color = payload.color
    if payload.is_active is not None:
        prof.is_active = payload.is_active
    if payload.is_bookable is not None:
        prof.is_bookable = payload.is_bookable
    if payload.sort_order is not None:
        prof.sort_order = payload.sort_order
    if payload.specialty_service_ids is not None:
        await _replace_specialties(db, tenant_id, prof.id, payload.specialty_service_ids)

    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_PROFESSIONAL_UPDATED,
        resource_type=RESOURCE_PROFESSIONAL,
        resource_id=prof.id,
        request_ctx=request_ctx,
    )
    return _to_read(await get_professional(db, tenant_id, prof.id))


async def _tenant_now(db: AsyncSession, tenant_id: UUID) -> datetime:
    settings = await business_hours_service.get_scheduling_settings(db, tenant_id)
    tz = resolve_display_timezone(settings.timezone)
    return datetime.now(tz)


async def _ensure_no_future_appointments_or_raise(
    db: AsyncSession,
    tenant_id: UUID,
    professional_id: UUID,
) -> None:
    now = await _tenant_now(db, tenant_id)
    result = await db.execute(
        select(func.count())
        .select_from(Appointment)
        .where(
            Appointment.tenant_id == tenant_id,
            Appointment.professional_id == professional_id,
            Appointment.status != AppointmentStatus.cancelled,
            Appointment.start_at >= now,
        )
    )
    count = result.scalar_one()
    if count > 0:
        raise ValidationError(
            "Professional has future appointments; reassign them before deactivating",
            details={"future_appointment_count": count},
        )


async def list_future_appointment_ids(
    db: AsyncSession,
    tenant_id: UUID,
    professional_id: UUID,
) -> list[UUID]:
    now = await _tenant_now(db, tenant_id)
    result = await db.execute(
        select(Appointment.id).where(
            Appointment.tenant_id == tenant_id,
            Appointment.professional_id == professional_id,
            Appointment.status != AppointmentStatus.cancelled,
            Appointment.start_at >= now.astimezone(UTC),
        )
    )
    return list(result.scalars().all())


async def count_future_appointments(
    db: AsyncSession,
    tenant_id: UUID,
    professional_id: UUID,
) -> int:
    now = await _tenant_now(db, tenant_id)
    result = await db.execute(
        select(func.count())
        .select_from(Appointment)
        .where(
            Appointment.tenant_id == tenant_id,
            Appointment.professional_id == professional_id,
            Appointment.status != AppointmentStatus.cancelled,
            Appointment.start_at >= now,
        )
    )
    return int(result.scalar_one())


async def reassign_future_appointments(
    db: AsyncSession,
    tenant_id: UUID,
    from_professional_id: UUID,
    payload: ReassignAppointmentsRequest,
    *,
    user_id: UUID | None = None,
    request_ctx: AuditRequestContext | None = None,
) -> int:
    target = await get_professional(db, tenant_id, payload.target_professional_id)
    if not target.is_active or not target.is_bookable:
        raise ValidationError("Target professional must be active and bookable")

    result = await db.execute(
        select(Appointment).where(
            Appointment.tenant_id == tenant_id,
            Appointment.id.in_(payload.appointment_ids),
            Appointment.professional_id == from_professional_id,
            Appointment.status != AppointmentStatus.cancelled,
        )
    )
    appointments = list(result.scalars().all())
    if len(appointments) != len(payload.appointment_ids):
        raise ValidationError("Some appointments were not found for reassignment")

    for appt in appointments:
        appt.professional_id = payload.target_professional_id

    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_APPOINTMENTS_REASSIGNED,
        resource_type=RESOURCE_PROFESSIONAL,
        resource_id=from_professional_id,
        metadata={
            "target_professional_id": str(payload.target_professional_id),
            "count": len(appointments),
        },
        request_ctx=request_ctx,
    )
    return len(appointments)


async def deactivate_professional(
    db: AsyncSession,
    tenant_id: UUID,
    professional_id: UUID,
    *,
    user_id: UUID | None = None,
    request_ctx: AuditRequestContext | None = None,
) -> ProfessionalRead:
    future_count = await count_future_appointments(db, tenant_id, professional_id)
    if future_count > 0:
        raise ValidationError(
            "Reassign future appointments before deactivating",
            details={"future_appointment_count": future_count},
        )
    return await update_professional(
        db,
        tenant_id,
        professional_id,
        ProfessionalUpdate(is_active=False),
        user_id=user_id,
        request_ctx=request_ctx,
    )


async def get_working_hours(
    db: AsyncSession,
    tenant_id: UUID,
    professional_id: UUID,
) -> list[ProfessionalWorkingHourRead]:
    await get_professional(db, tenant_id, professional_id)
    result = await db.execute(
        select(ProfessionalWorkingHour)
        .where(
            ProfessionalWorkingHour.tenant_id == tenant_id,
            ProfessionalWorkingHour.professional_id == professional_id,
        )
        .order_by(ProfessionalWorkingHour.weekday, ProfessionalWorkingHour.sort_order)
    )
    return [ProfessionalWorkingHourRead.model_validate(row) for row in result.scalars().all()]


async def replace_working_hours(
    db: AsyncSession,
    tenant_id: UUID,
    professional_id: UUID,
    payload: ProfessionalWorkingHoursUpdate,
    *,
    user_id: UUID | None = None,
    request_ctx: AuditRequestContext | None = None,
) -> list[ProfessionalWorkingHourRead]:
    await get_professional(db, tenant_id, professional_id)
    await db.execute(
        delete(ProfessionalWorkingHour).where(
            ProfessionalWorkingHour.tenant_id == tenant_id,
            ProfessionalWorkingHour.professional_id == professional_id,
        )
    )
    for slot in payload.slots:
        db.add(
            ProfessionalWorkingHour(
                tenant_id=tenant_id,
                professional_id=professional_id,
                weekday=slot.weekday,
                sort_order=slot.sort_order,
                opens_at=slot.opens_at,
                closes_at=slot.closes_at,
            )
        )
    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_PROFESSIONAL_UPDATED,
        resource_type=RESOURCE_PROFESSIONAL,
        resource_id=professional_id,
        metadata={"working_hours_updated": True},
        request_ctx=request_ctx,
    )
    return await get_working_hours(db, tenant_id, professional_id)


async def list_bookable_professionals(db: AsyncSession, tenant_id: UUID) -> list[Professional]:
    result = await db.execute(
        select(Professional)
        .where(
            Professional.tenant_id == tenant_id,
            Professional.is_active.is_(True),
            Professional.is_bookable.is_(True),
        )
        .order_by(Professional.sort_order, Professional.display_name)
    )
    return list(result.scalars().all())
