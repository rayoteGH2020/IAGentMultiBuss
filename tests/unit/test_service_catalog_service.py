"""Tests del catálogo de servicios (Paso 30 §E.3)."""

from __future__ import annotations

import pytest
from app.core.db import set_tenant_context
from app.core.errors import NotFoundError
from app.models import Tenant
from app.schemas.scheduling import (
    MAX_SERVICE_DURATION,
    MIN_SERVICE_DURATION,
    SchedulingServiceCreate,
    SchedulingServiceUpdate,
)
from app.services import service_catalog_service
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_and_list_services(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    created = await service_catalog_service.create_service(
        db_session,
        tenant.id,
        SchedulingServiceCreate(name="Masaje", duration_minutes=60),
    )
    assert created.slug == "masaje"
    assert created.duration_minutes == 60

    services = await service_catalog_service.list_services(db_session, tenant.id)
    assert len(services) == 1
    assert services[0].id == created.id


@pytest.mark.asyncio
@pytest.mark.integration
async def test_update_service_name_regenerates_slug(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    created = await service_catalog_service.create_service(
        db_session,
        tenant.id,
        SchedulingServiceCreate(name="Corte", duration_minutes=30),
    )
    updated = await service_catalog_service.update_service(
        db_session,
        tenant.id,
        created.id,
        SchedulingServiceUpdate(name="Corte Premium"),
    )
    assert updated.slug == "corte-premium"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_service_not_found(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
) -> None:
    from uuid import uuid4

    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    with pytest.raises(NotFoundError, match="Service not found"):
        await service_catalog_service.get_service(db_session, tenant.id, uuid4())


def test_create_service_rejects_duration_below_minimum() -> None:
    with pytest.raises(PydanticValidationError):
        SchedulingServiceCreate(name="X", duration_minutes=MIN_SERVICE_DURATION - 1)


def test_create_service_rejects_duration_above_maximum() -> None:
    with pytest.raises(PydanticValidationError):
        SchedulingServiceCreate(name="X", duration_minutes=MAX_SERVICE_DURATION + 1)


def test_update_service_rejects_invalid_duration() -> None:
    with pytest.raises(PydanticValidationError):
        SchedulingServiceUpdate(duration_minutes=10)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_require_active_service_rejects_inactive(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
) -> None:
    from app.core.errors import ValidationError

    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    created = await service_catalog_service.create_service(
        db_session,
        tenant.id,
        SchedulingServiceCreate(name="Legacy", duration_minutes=30, is_active=False),
    )
    with pytest.raises(ValidationError, match="inactive"):
        await service_catalog_service.require_active_service(db_session, tenant.id, created.id)
