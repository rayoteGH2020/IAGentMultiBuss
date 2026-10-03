"""Horario del centro, excepciones y settings de scheduling (Paso 30 Fase B)."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.errors import NotFoundError, ValidationError
from app.core.scheduling_defaults import DEFAULT_SCHEDULING_SETTINGS
from app.models.business_hour import BusinessHour
from app.models.schedule_exception import ScheduleException
from app.models.tenant import Tenant
from app.schemas.scheduling import (
    BusinessHourRead,
    BusinessHoursUpdate,
    ScheduleExceptionCreate,
    ScheduleExceptionRead,
    TenantSchedulingSettingsRead,
    TenantSchedulingSettingsUpdate,
)
from app.services import audit_service

if TYPE_CHECKING:
    from app.services.audit_service import AuditRequestContext

ACTION_BUSINESS_HOURS_UPDATED = "scheduling.business_hours_updated"
ACTION_SCHEDULE_EXCEPTION_CREATED = "scheduling.schedule_exception_created"
ACTION_SCHEDULE_EXCEPTION_REMOVED = "scheduling.schedule_exception_removed"
ACTION_SCHEDULING_SETTINGS_UPDATED = "scheduling.settings_updated"
RESOURCE_SCHEDULING = "scheduling"


def _validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValidationError(f"Invalid IANA timezone: {value}") from exc
    return value


async def get_business_hours(db: AsyncSession, tenant_id: UUID) -> list[BusinessHourRead]:
    result = await db.execute(
        select(BusinessHour)
        .where(BusinessHour.tenant_id == tenant_id)
        .order_by(BusinessHour.weekday, BusinessHour.sort_order)
    )
    return [BusinessHourRead.model_validate(row) for row in result.scalars().all()]


async def replace_business_hours(
    db: AsyncSession,
    tenant_id: UUID,
    payload: BusinessHoursUpdate,
    *,
    user_id: UUID | None = None,
    request_ctx: AuditRequestContext | None = None,
) -> list[BusinessHourRead]:
    await db.execute(delete(BusinessHour).where(BusinessHour.tenant_id == tenant_id))
    for slot in payload.slots:
        db.add(
            BusinessHour(
                tenant_id=tenant_id,
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
        action=ACTION_BUSINESS_HOURS_UPDATED,
        resource_type=RESOURCE_SCHEDULING,
        metadata={"slot_count": len(payload.slots)},
        request_ctx=request_ctx,
    )
    return await get_business_hours(db, tenant_id)


async def list_schedule_exceptions(
    db: AsyncSession,
    tenant_id: UUID,
) -> list[ScheduleExceptionRead]:
    result = await db.execute(
        select(ScheduleException)
        .where(ScheduleException.tenant_id == tenant_id)
        .order_by(ScheduleException.exception_date)
    )
    return [ScheduleExceptionRead.model_validate(row) for row in result.scalars().all()]


async def create_schedule_exception(
    db: AsyncSession,
    tenant_id: UUID,
    payload: ScheduleExceptionCreate,
    *,
    user_id: UUID | None = None,
    request_ctx: AuditRequestContext | None = None,
) -> ScheduleExceptionRead:
    row = ScheduleException(
        tenant_id=tenant_id,
        exception_date=payload.exception_date,
        label=payload.label,
        is_closed=payload.is_closed,
    )
    db.add(row)
    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_SCHEDULE_EXCEPTION_CREATED,
        resource_type=RESOURCE_SCHEDULING,
        resource_id=row.id,
        metadata={"exception_date": payload.exception_date.isoformat()},
        request_ctx=request_ctx,
    )
    return ScheduleExceptionRead.model_validate(row)


async def delete_schedule_exception(
    db: AsyncSession,
    tenant_id: UUID,
    exception_id: UUID,
    *,
    user_id: UUID | None = None,
    request_ctx: AuditRequestContext | None = None,
) -> None:
    result = await db.execute(
        select(ScheduleException).where(
            ScheduleException.id == exception_id,
            ScheduleException.tenant_id == tenant_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError("Schedule exception not found")
    await db.delete(row)
    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_SCHEDULE_EXCEPTION_REMOVED,
        resource_type=RESOURCE_SCHEDULING,
        resource_id=exception_id,
        request_ctx=request_ctx,
    )


async def _get_tenant(db: AsyncSession, tenant_id: UUID) -> Tenant:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise NotFoundError("Tenant not found")
    return tenant


async def get_scheduling_settings(
    db: AsyncSession,
    tenant_id: UUID,
) -> TenantSchedulingSettingsRead:
    tenant = await _get_tenant(db, tenant_id)
    raw = tenant.settings.get("scheduling", {})
    merged = (
        {**DEFAULT_SCHEDULING_SETTINGS, **raw}
        if isinstance(raw, dict)
        else dict(DEFAULT_SCHEDULING_SETTINGS)
    )
    return TenantSchedulingSettingsRead.model_validate(merged)


async def update_scheduling_settings(
    db: AsyncSession,
    tenant_id: UUID,
    payload: TenantSchedulingSettingsUpdate,
    *,
    user_id: UUID | None = None,
    request_ctx: AuditRequestContext | None = None,
) -> TenantSchedulingSettingsRead:
    tenant = await _get_tenant(db, tenant_id)
    current = await get_scheduling_settings(db, tenant_id)
    data = current.model_dump()
    updates = payload.model_dump(exclude_unset=True)
    if "timezone" in updates and updates["timezone"] is not None:
        updates["timezone"] = _validate_timezone(updates["timezone"])
    data.update(updates)
    settings = deepcopy(tenant.settings)
    settings["scheduling"] = data
    tenant.settings = settings
    flag_modified(tenant, "settings")
    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_SCHEDULING_SETTINGS_UPDATED,
        resource_type=RESOURCE_SCHEDULING,
        metadata=updates,
        request_ctx=request_ctx,
    )
    return TenantSchedulingSettingsRead.model_validate(data)


async def get_closed_dates(
    db: AsyncSession,
    tenant_id: UUID,
    range_start: date,
    range_end: date,
) -> set[date]:
    result = await db.execute(
        select(ScheduleException.exception_date).where(
            ScheduleException.tenant_id == tenant_id,
            ScheduleException.is_closed.is_(True),
            ScheduleException.exception_date >= range_start,
            ScheduleException.exception_date <= range_end,
        )
    )
    return set(result.scalars().all())
