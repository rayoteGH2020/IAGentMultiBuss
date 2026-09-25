"""Tests del servicio de citas internas (Paso 30 §E.2)."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.core.errors import ForbiddenError, ValidationError
from app.models import Membership, Tenant
from app.schemas.scheduling import (
    AppointmentCancel,
    AppointmentCreate,
    AppointmentStatus,
    AppointmentUpdate,
    SchedulingServiceUpdate,
)
from app.services import internal_appointment_service, service_catalog_service
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.scheduling_test_helpers import (
    FIXED_SCHEDULING_NOW,
    future_appointment_start,
    patch_scheduling_now,
    seed_scheduling_catalog,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture
def freeze_scheduling_now(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_scheduling_now(monkeypatch, "app.services.internal_appointment_service")
    patch_scheduling_now(monkeypatch, "app.services.appointment_slot_service")


async def _env(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
) -> tuple[Tenant, object, object]:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    service_id, professional_id = await seed_scheduling_catalog(db_session, tenant.id)
    return tenant, service_id, professional_id


async def test_create_without_professional_id(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_scheduling_now: None,
) -> None:
    tenant, service_id, _ = await _env(db_session, tenant_factory, scheduling_schema_ready)
    start = future_appointment_start()
    row = await internal_appointment_service.create_appointment(
        db_session,
        tenant.id,
        AppointmentCreate(
            service_id=service_id,
            professional_id=None,
            start_at=start,
            client_name="Sin Pro",
            client_phone="600111222",
        ),
    )
    assert row.professional_id is None
    assert row.client_name == "Sin Pro"


async def test_create_rejects_inactive_service(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_scheduling_now: None,
) -> None:
    tenant, service_id, professional_id = await _env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    await service_catalog_service.update_service(
        db_session,
        tenant.id,
        service_id,
        SchedulingServiceUpdate(is_active=False),
    )
    with pytest.raises(ValidationError, match="inactive"):
        await internal_appointment_service.create_appointment(
            db_session,
            tenant.id,
            AppointmentCreate(
                service_id=service_id,
                professional_id=professional_id,
                start_at=future_appointment_start(),
                client_name="Ana",
                client_phone="600111222",
            ),
        )


async def test_end_at_derived_from_duration(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_scheduling_now: None,
) -> None:
    tenant, service_id, professional_id = await _env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    start = future_appointment_start(hour=11, minute=0)
    row = await internal_appointment_service.create_appointment(
        db_session,
        tenant.id,
        AppointmentCreate(
            service_id=service_id,
            professional_id=professional_id,
            start_at=start,
            client_name="Ana",
            client_phone="600111222",
        ),
    )
    assert row.end_at == start + timedelta(minutes=30)


async def test_create_rejects_non_grid_start(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_scheduling_now: None,
) -> None:
    tenant, service_id, professional_id = await _env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    bad_start = future_appointment_start(hour=10, minute=7)
    with pytest.raises(ValidationError):
        await internal_appointment_service.create_appointment(
            db_session,
            tenant.id,
            AppointmentCreate(
                service_id=service_id,
                professional_id=professional_id,
                start_at=bad_start,
                client_name="Ana",
                client_phone="600111222",
            ),
        )


async def test_create_rejects_buffer_violation(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_scheduling_now: None,
) -> None:
    tenant, service_id, professional_id = await _env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    first = future_appointment_start(hour=10, minute=0)
    await internal_appointment_service.create_appointment(
        db_session,
        tenant.id,
        AppointmentCreate(
            service_id=service_id,
            professional_id=professional_id,
            start_at=first,
            client_name="Primera",
            client_phone="600111222",
        ),
    )
    with pytest.raises(ValidationError, match="overlap"):
        await internal_appointment_service.create_appointment(
            db_session,
            tenant.id,
            AppointmentCreate(
                service_id=service_id,
                professional_id=professional_id,
                start_at=first + timedelta(minutes=30),
                client_name="Segunda",
                client_phone="600333444",
            ),
        )


async def test_update_rejects_cancelled_appointment(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_scheduling_now: None,
) -> None:
    tenant, service_id, professional_id = await _env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    start = future_appointment_start(hour=12, minute=0)
    created = await internal_appointment_service.create_appointment(
        db_session,
        tenant.id,
        AppointmentCreate(
            service_id=service_id,
            professional_id=professional_id,
            start_at=start,
            client_name="Ana",
            client_phone="600111222",
        ),
    )
    await internal_appointment_service.cancel_appointment(
        db_session,
        tenant.id,
        created.id,
        AppointmentCancel(cancellation_reason="test"),
    )
    with pytest.raises(ValidationError, match="Cancelled"):
        await internal_appointment_service.update_appointment(
            db_session,
            tenant.id,
            created.id,
            AppointmentUpdate(
                service_id=service_id,
                professional_id=professional_id,
                start_at=start,
                client_name="Ana",
                client_phone="600111222",
            ),
        )


async def test_update_excludes_self_from_overlap(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_scheduling_now: None,
) -> None:
    tenant, service_id, professional_id = await _env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    start = future_appointment_start(hour=10, minute=0)
    created = await internal_appointment_service.create_appointment(
        db_session,
        tenant.id,
        AppointmentCreate(
            service_id=service_id,
            professional_id=professional_id,
            start_at=start,
            client_name="Ana",
            client_phone="600111222",
        ),
    )
    updated = await internal_appointment_service.update_appointment(
        db_session,
        tenant.id,
        created.id,
        AppointmentUpdate(
            service_id=service_id,
            professional_id=professional_id,
            start_at=start,
            client_name="Ana Actualizada",
            client_phone="600111222",
            notes="ok",
        ),
    )
    assert updated.client_name == "Ana Actualizada"


async def test_status_confirmed_requires_create_or_edit(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_scheduling_now: None,
) -> None:
    tenant, service_id, professional_id = await _env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    start = future_appointment_start(hour=16, minute=0)
    created = await internal_appointment_service.create_appointment(
        db_session,
        tenant.id,
        AppointmentCreate(
            service_id=service_id,
            professional_id=professional_id,
            start_at=start,
            client_name="Ana",
            client_phone="600111222",
        ),
    )
    membership = Membership(
        user_id=uuid4(),
        tenant_id=tenant.id,
        role="member",
        permissions={
            "appointments": {"view": True, "create": False, "edit": False, "cancel": False}
        },
    )
    with pytest.raises(ForbiddenError, match="confirm"):
        await internal_appointment_service.update_appointment_status(
            db_session,
            tenant.id,
            created.id,
            AppointmentStatus.confirmed,
            membership,
        )


async def test_status_completed_allowed_with_view_only(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_scheduling_now: None,
) -> None:
    tenant, service_id, professional_id = await _env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    start = future_appointment_start(hour=17, minute=0)
    created = await internal_appointment_service.create_appointment(
        db_session,
        tenant.id,
        AppointmentCreate(
            service_id=service_id,
            professional_id=professional_id,
            start_at=start,
            client_name="Ana",
            client_phone="600111222",
        ),
    )
    membership = Membership(
        user_id=uuid4(),
        tenant_id=tenant.id,
        role="member",
        permissions={
            "appointments": {"view": True, "create": False, "edit": False, "cancel": False}
        },
    )
    updated = await internal_appointment_service.update_appointment_status(
        db_session,
        tenant.id,
        created.id,
        AppointmentStatus.completed,
        membership,
    )
    assert updated.status == AppointmentStatus.completed


async def test_create_rejects_empty_client_fields(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_scheduling_now: None,
) -> None:
    tenant, service_id, professional_id = await _env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    with pytest.raises(ValidationError, match="client_name"):
        await internal_appointment_service.create_appointment(
            db_session,
            tenant.id,
            AppointmentCreate(
                service_id=service_id,
                professional_id=professional_id,
                start_at=future_appointment_start(),
                client_name="   ",
                client_phone="600111222",
            ),
        )


async def test_create_rejects_inactive_professional(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_scheduling_now: None,
) -> None:
    from app.schemas.scheduling import ProfessionalUpdate
    from app.services import professional_service

    tenant, service_id, professional_id = await _env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    await professional_service.update_professional(
        db_session,
        tenant.id,
        professional_id,
        ProfessionalUpdate(is_active=False),
    )
    with pytest.raises(ValidationError, match="inactive"):
        await internal_appointment_service.create_appointment(
            db_session,
            tenant.id,
            AppointmentCreate(
                service_id=service_id,
                professional_id=professional_id,
                start_at=future_appointment_start(hour=18, minute=0),
                client_name="Ana",
                client_phone="600111222",
            ),
        )


async def test_cancel_keeps_row_and_frees_slot(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_scheduling_now: None,
) -> None:
    from app.schemas.scheduling import FindSlotsRequest
    from app.services import appointment_slot_service

    tenant, service_id, professional_id = await _env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    start = future_appointment_start(hour=9, minute=0)
    created = await internal_appointment_service.create_appointment(
        db_session,
        tenant.id,
        AppointmentCreate(
            service_id=service_id,
            professional_id=professional_id,
            start_at=start,
            client_name="Ana",
            client_phone="600111222",
        ),
    )
    await internal_appointment_service.cancel_appointment(
        db_session,
        tenant.id,
        created.id,
        AppointmentCancel(),
    )
    still = await internal_appointment_service.get_appointment(db_session, tenant.id, created.id)
    assert still.status == AppointmentStatus.cancelled

    slots = await appointment_slot_service.find_next_available_slots(
        db_session,
        tenant.id,
        FindSlotsRequest(
            service_id=service_id,
            professional_id=professional_id,
            after=start,
            count=1,
        ),
    )
    assert slots.slots[0].start == start


async def test_double_booking_same_professional_rejected(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_scheduling_now: None,
) -> None:
    tenant, service_id, professional_id = await _env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    start = future_appointment_start(hour=9, minute=30)
    await internal_appointment_service.create_appointment(
        db_session,
        tenant.id,
        AppointmentCreate(
            service_id=service_id,
            professional_id=professional_id,
            start_at=start,
            client_name="Primera",
            client_phone="600111222",
        ),
    )
    with pytest.raises(ValidationError, match="overlap"):
        await internal_appointment_service.create_appointment(
            db_session,
            tenant.id,
            AppointmentCreate(
                service_id=service_id,
                professional_id=professional_id,
                start_at=start,
                client_name="Segunda",
                client_phone="600333444",
            ),
        )


async def test_update_rejects_inactive_service(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_scheduling_now: None,
) -> None:
    tenant, service_id, professional_id = await _env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    start = future_appointment_start(hour=11, minute=0)
    created = await internal_appointment_service.create_appointment(
        db_session,
        tenant.id,
        AppointmentCreate(
            service_id=service_id,
            professional_id=professional_id,
            start_at=start,
            client_name="Ana",
            client_phone="600111222",
        ),
    )
    await service_catalog_service.update_service(
        db_session,
        tenant.id,
        service_id,
        SchedulingServiceUpdate(is_active=False),
    )
    with pytest.raises(ValidationError, match="inactive"):
        await internal_appointment_service.update_appointment(
            db_session,
            tenant.id,
            created.id,
            AppointmentUpdate(
                service_id=service_id,
                professional_id=professional_id,
                start_at=start,
                client_name="Ana",
                client_phone="600111222",
            ),
        )


async def test_update_rejects_past_day_appointment(
    db_session: AsyncSession,
    tenant_factory: object,
    scheduling_schema_ready: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cita de ayer: solo lectura a nivel servicio."""
    from datetime import datetime, timedelta

    tz = FIXED_SCHEDULING_NOW.tzinfo
    yesterday = FIXED_SCHEDULING_NOW.date() - timedelta(days=1)
    today = FIXED_SCHEDULING_NOW.date()
    create_now = datetime(yesterday.year, yesterday.month, yesterday.day, 8, 0, tzinfo=tz)

    import datetime as dt_module

    real_datetime = dt_module.datetime

    class CreatePhaseDatetime(real_datetime):
        @classmethod
        def now(cls, tzinfo=None):
            if tzinfo is not None:
                return create_now.astimezone(tzinfo)
            return create_now

    monkeypatch.setattr(
        "app.services.internal_appointment_service.display_today",
        lambda timezone=None: yesterday,
    )
    monkeypatch.setattr("app.services.internal_appointment_service.datetime", CreatePhaseDatetime)
    monkeypatch.setattr("app.services.appointment_slot_service.datetime", CreatePhaseDatetime)

    tenant, service_id, professional_id = await _env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    start = datetime(yesterday.year, yesterday.month, yesterday.day, 10, 0, tzinfo=tz)
    created = await internal_appointment_service.create_appointment(
        db_session,
        tenant.id,
        AppointmentCreate(
            service_id=service_id,
            professional_id=professional_id,
            start_at=start,
            client_name="Ana",
            client_phone="600111222",
        ),
    )

    monkeypatch.setattr(
        "app.services.internal_appointment_service.display_today",
        lambda timezone=None: today,
    )
    patch_scheduling_now(monkeypatch, "app.services.internal_appointment_service")
    patch_scheduling_now(monkeypatch, "app.services.appointment_slot_service")
    with pytest.raises(ValidationError, match="read-only"):
        await internal_appointment_service.update_appointment(
            db_session,
            tenant.id,
            created.id,
            AppointmentUpdate(
                service_id=service_id,
                professional_id=professional_id,
                start_at=start,
                client_name="No",
                client_phone="600111222",
            ),
        )
