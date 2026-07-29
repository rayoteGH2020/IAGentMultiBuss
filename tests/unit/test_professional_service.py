"""Tests del servicio de profesionales (Paso 30 §E.3)."""

from __future__ import annotations

import pytest
from app.core.db import set_tenant_context
from app.core.errors import NotFoundError, ValidationError
from app.models import Tenant
from app.schemas.scheduling import (
    AppointmentCreate,
    ProfessionalCreate,
    ProfessionalUpdate,
    ReassignAppointmentsRequest,
    SchedulingServiceCreate,
)
from app.services import internal_appointment_service, professional_service, service_catalog_service
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.scheduling_test_helpers import (
    future_appointment_start,
    patch_scheduling_now,
    seed_default_business_hours,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def freeze_scheduling_now(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_scheduling_now(monkeypatch, "app.services.internal_appointment_service")
    patch_scheduling_now(monkeypatch, "app.services.professional_service")


async def _services(
    db_session: AsyncSession,
    tenant_id: object,
    count: int,
) -> list[object]:
    ids: list[object] = []
    for idx in range(count):
        row = await service_catalog_service.create_service(
            db_session,
            tenant_id,  # type: ignore[arg-type]
            SchedulingServiceCreate(name=f"Servicio {idx + 1}", duration_minutes=30),
        )
        ids.append(row.id)
    return ids


@pytest.mark.asyncio
async def test_create_professional_copies_center_hours(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    await seed_default_business_hours(db_session, tenant.id)

    prof = await professional_service.create_professional(
        db_session,
        tenant.id,
        ProfessionalCreate(display_name="Ana"),
    )
    working = await professional_service.get_working_hours(db_session, tenant.id, prof.id)
    assert len(working) == 10


@pytest.mark.asyncio
async def test_professional_accepts_up_to_three_specialties(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    service_ids = await _services(db_session, tenant.id, 3)

    prof = await professional_service.create_professional(
        db_session,
        tenant.id,
        ProfessionalCreate(display_name="Especialista", specialty_service_ids=service_ids),  # type: ignore[arg-type]
    )
    assert prof.specialty_service_ids == service_ids


@pytest.mark.asyncio
async def test_replace_specialties_rejects_fourth_id(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
) -> None:
    from app.services import professional_service as prof_mod

    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    service_ids = await _services(db_session, tenant.id, 4)
    prof = await professional_service.create_professional(
        db_session,
        tenant.id,
        ProfessionalCreate(display_name="Tres"),
    )

    with pytest.raises(ValidationError, match="At most 3"):
        await prof_mod._replace_specialties(db_session, tenant.id, prof.id, service_ids)  # type: ignore[arg-type]


def test_professional_create_schema_rejects_fourth_specialty() -> None:
    from uuid import uuid4

    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        ProfessionalCreate(
            display_name="Exceso",
            specialty_service_ids=[uuid4() for _ in range(4)],
        )


@pytest.mark.asyncio
async def test_professional_rejects_inactive_specialty_service(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    inactive = await service_catalog_service.create_service(
        db_session,
        tenant.id,
        SchedulingServiceCreate(name="Inactivo", duration_minutes=30, is_active=False),
    )

    with pytest.raises(ValidationError, match="active"):
        await professional_service.create_professional(
            db_session,
            tenant.id,
            ProfessionalCreate(display_name="Ana", specialty_service_ids=[inactive.id]),
        )


@pytest.mark.asyncio
async def test_deactivate_blocked_with_future_appointments(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_scheduling_now: None,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    await seed_default_business_hours(db_session, tenant.id)
    service = await service_catalog_service.create_service(
        db_session,
        tenant.id,
        SchedulingServiceCreate(name="Consulta", duration_minutes=30),
    )
    source = await professional_service.create_professional(
        db_session,
        tenant.id,
        ProfessionalCreate(display_name="Origen"),
    )
    await internal_appointment_service.create_appointment(
        db_session,
        tenant.id,
        AppointmentCreate(
            service_id=service.id,
            professional_id=source.id,
            start_at=future_appointment_start(hour=10, minute=0),
            client_name="Cliente",
            client_phone="600111222",
        ),
    )

    with pytest.raises(ValidationError, match="Reassign"):
        await professional_service.deactivate_professional(db_session, tenant.id, source.id)


@pytest.mark.asyncio
async def test_reassign_then_deactivate_professional(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_scheduling_now: None,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    await seed_default_business_hours(db_session, tenant.id)
    service = await service_catalog_service.create_service(
        db_session,
        tenant.id,
        SchedulingServiceCreate(name="Consulta", duration_minutes=30),
    )
    source = await professional_service.create_professional(
        db_session,
        tenant.id,
        ProfessionalCreate(display_name="Origen"),
    )
    target = await professional_service.create_professional(
        db_session,
        tenant.id,
        ProfessionalCreate(display_name="Destino"),
    )
    appt = await internal_appointment_service.create_appointment(
        db_session,
        tenant.id,
        AppointmentCreate(
            service_id=service.id,
            professional_id=source.id,
            start_at=future_appointment_start(hour=11, minute=0),
            client_name="Cliente",
            client_phone="600111222",
        ),
    )

    moved = await professional_service.reassign_future_appointments(
        db_session,
        tenant.id,
        source.id,
        ReassignAppointmentsRequest(
            target_professional_id=target.id,
            appointment_ids=[appt.id],
        ),
    )
    assert moved == 1

    deactivated = await professional_service.deactivate_professional(
        db_session,
        tenant.id,
        source.id,
    )
    assert deactivated.is_active is False

    refreshed = await internal_appointment_service.get_appointment(db_session, tenant.id, appt.id)
    assert refreshed.professional_id == target.id


@pytest.mark.asyncio
async def test_reassign_rejects_inactive_target(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_scheduling_now: None,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    await seed_default_business_hours(db_session, tenant.id)
    service = await service_catalog_service.create_service(
        db_session,
        tenant.id,
        SchedulingServiceCreate(name="Consulta", duration_minutes=30),
    )
    source = await professional_service.create_professional(
        db_session,
        tenant.id,
        ProfessionalCreate(display_name="Origen"),
    )
    target = await professional_service.create_professional(
        db_session,
        tenant.id,
        ProfessionalCreate(display_name="Destino"),
    )
    await professional_service.update_professional(
        db_session,
        tenant.id,
        target.id,
        ProfessionalUpdate(is_active=False),
    )
    appt = await internal_appointment_service.create_appointment(
        db_session,
        tenant.id,
        AppointmentCreate(
            service_id=service.id,
            professional_id=source.id,
            start_at=future_appointment_start(hour=12, minute=0),
            client_name="Cliente",
            client_phone="600111222",
        ),
    )

    with pytest.raises(ValidationError, match="active and bookable"):
        await professional_service.reassign_future_appointments(
            db_session,
            tenant.id,
            source.id,
            ReassignAppointmentsRequest(
                target_professional_id=target.id,
                appointment_ids=[appt.id],
            ),
        )


@pytest.mark.asyncio
async def test_get_professional_not_found(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
) -> None:
    from uuid import uuid4

    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    with pytest.raises(NotFoundError, match="Professional not found"):
        await professional_service.get_professional(db_session, tenant.id, uuid4())
