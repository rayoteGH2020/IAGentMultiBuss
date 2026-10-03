"""Tests de horario del centro y settings (Paso 30 §E.3)."""

from __future__ import annotations

from datetime import date, time

import pytest
from app.core.db import set_tenant_context
from app.core.errors import NotFoundError
from app.core.scheduling_defaults import DEFAULT_BUSINESS_HOUR_SEEDS, DEFAULT_SCHEDULING_SETTINGS
from app.models import Tenant
from app.schemas.scheduling import (
    BusinessHourSlotUpdate,
    BusinessHoursUpdate,
    ScheduleExceptionCreate,
    TenantSchedulingSettingsUpdate,
)
from app.services import business_hours_service
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.scheduling_test_helpers import parse_seed_time, seed_default_business_hours

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_default_business_hours_match_lv_seed(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    await seed_default_business_hours(db_session, tenant.id)

    hours = await business_hours_service.get_business_hours(db_session, tenant.id)
    assert len(hours) == len(DEFAULT_BUSINESS_HOUR_SEEDS)

    by_key = {(row.weekday, row.sort_order): row for row in hours}
    for seed in DEFAULT_BUSINESS_HOUR_SEEDS:
        key = (seed["weekday"], seed["sort_order"])
        row = by_key[key]
        assert row.opens_at == parse_seed_time(seed["opens_at"])
        assert row.closes_at == parse_seed_time(seed["closes_at"])

    weekdays = {row.weekday for row in hours}
    assert weekdays == {0, 1, 2, 3, 4}
    morning = [row for row in hours if row.sort_order == 0]
    assert all(row.opens_at == time(9, 0) and row.closes_at == time(14, 0) for row in morning)
    afternoon = [row for row in hours if row.sort_order == 1]
    assert all(row.opens_at == time(16, 0) and row.closes_at == time(21, 0) for row in afternoon)


async def test_replace_business_hours_overwrites_previous(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    await seed_default_business_hours(db_session, tenant.id)

    updated = await business_hours_service.replace_business_hours(
        db_session,
        tenant.id,
        BusinessHoursUpdate(
            slots=[
                BusinessHourSlotUpdate(
                    weekday=0,
                    sort_order=0,
                    opens_at=time(10, 0),
                    closes_at=time(13, 0),
                )
            ]
        ),
    )
    assert len(updated) == 1
    assert updated[0].opens_at == time(10, 0)


async def test_scheduling_settings_defaults(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    settings = await business_hours_service.get_scheduling_settings(db_session, tenant.id)
    assert settings.timezone == DEFAULT_SCHEDULING_SETTINGS["timezone"]
    assert settings.search_horizon_days == DEFAULT_SCHEDULING_SETTINGS["search_horizon_days"]
    assert (
        settings.slot_granularity_minutes == DEFAULT_SCHEDULING_SETTINGS["slot_granularity_minutes"]
    )
    assert settings.buffer_minutes == DEFAULT_SCHEDULING_SETTINGS["buffer_minutes"]


async def test_update_scheduling_settings(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    updated = await business_hours_service.update_scheduling_settings(
        db_session,
        tenant.id,
        TenantSchedulingSettingsUpdate(buffer_minutes=5),
    )
    assert updated.buffer_minutes == 5


async def test_schedule_exception_closed_date(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    closed = date(2026, 12, 25)
    row = await business_hours_service.create_schedule_exception(
        db_session,
        tenant.id,
        ScheduleExceptionCreate(exception_date=closed, label="Navidad", is_closed=True),
    )
    assert row.exception_date == closed

    closed_dates = await business_hours_service.get_closed_dates(
        db_session,
        tenant.id,
        closed,
        closed,
    )
    assert closed in closed_dates

    await business_hours_service.delete_schedule_exception(db_session, tenant.id, row.id)
    closed_dates_after = await business_hours_service.get_closed_dates(
        db_session,
        tenant.id,
        closed,
        closed,
    )
    assert closed not in closed_dates_after


async def test_delete_schedule_exception_not_found(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
) -> None:
    from uuid import uuid4

    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    with pytest.raises(NotFoundError, match="Schedule exception not found"):
        await business_hours_service.delete_schedule_exception(db_session, tenant.id, uuid4())
