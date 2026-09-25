"""Tests de helpers puros del motor de huecos (Paso 30 Fase B)."""

from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from app.core.db import set_tenant_context
from app.core.errors import ValidationError
from app.models import Tenant
from app.models.appointment import Appointment
from app.schemas.scheduling import (
    AppointmentCancel,
    AppointmentCreate,
    AppointmentStatus,
    AvailableSlot,
    FindSlotsRequest,
    ProfessionalCreate,
    ProfessionalUpdate,
    ProfessionalWorkingHourSlotUpdate,
    ProfessionalWorkingHoursUpdate,
    ScheduleExceptionCreate,
    SchedulingServiceCreate,
    SchedulingServiceUpdate,
)
from app.services import (
    appointment_slot_service,
    business_hours_service,
    internal_appointment_service,
    professional_service,
    service_catalog_service,
)
from app.services.appointment_slot_service import (
    HourRange,
    OccupiedBlock,
    SlotCandidate,
    _filter_overlaps,
    _generate_candidates,
    _intersect_hours,
    _intervals_overlap,
    _normalize_after,
    _same_start_time_warning,
)

from tests.integration.scheduling_test_helpers import (
    FIXED_SCHEDULING_NOW,
    future_appointment_start,
    patch_scheduling_now,
    seed_default_business_hours,
    seed_scheduling_catalog,
)

TZ = ZoneInfo("Europe/Madrid")


def test_intersect_hours_morning_and_afternoon() -> None:
    center = [HourRange(time(9, 0), time(14, 0)), HourRange(time(16, 0), time(21, 0))]
    prof = [HourRange(time(10, 0), time(13, 0)), HourRange(time(17, 0), time(20, 0))]
    hits = _intersect_hours(center, prof)
    assert len(hits) == 2
    assert hits[0].opens_at == time(10, 0)
    assert hits[1].opens_at == time(17, 0)


def test_generate_candidates_respects_duration() -> None:
    day = date(2026, 7, 15)
    after = datetime(2026, 7, 15, 9, 0, tzinfo=TZ)
    horizon_end = datetime(2026, 7, 15, 23, 0, tzinfo=TZ)
    ranges = [HourRange(time(9, 0), time(10, 0))]
    starts = _generate_candidates(ranges, day, TZ, after, 15, 30, horizon_end)
    assert starts[0] == datetime(2026, 7, 15, 9, 0, tzinfo=TZ)
    assert all(s.minute % 15 == 0 for s in starts)


def test_generate_candidates_uses_configurable_granularity() -> None:
    day = date(2026, 7, 15)
    after = datetime(2026, 7, 15, 9, 0, tzinfo=TZ)
    horizon_end = datetime(2026, 7, 15, 23, 0, tzinfo=TZ)
    ranges = [HourRange(time(9, 0), time(10, 30))]
    starts = _generate_candidates(ranges, day, TZ, after, 30, 30, horizon_end)
    assert starts == [
        datetime(2026, 7, 15, 9, 0, tzinfo=TZ),
        datetime(2026, 7, 15, 9, 30, tzinfo=TZ),
        datetime(2026, 7, 15, 10, 0, tzinfo=TZ),
    ]


def test_filter_overlaps_excludes_blocked_slot() -> None:
    prof_id = uuid4()
    start = datetime(2026, 7, 15, 10, 0, tzinfo=TZ)
    candidate = SlotCandidate(professional_id=prof_id, professional_name="A", start_at=start)
    occupied = [
        OccupiedBlock(
            professional_id=prof_id,
            start_at=start,
            end_at=datetime(2026, 7, 15, 10, 30, tzinfo=TZ),
        )
    ]
    free = _filter_overlaps([candidate], 30, 10, occupied)
    assert free == []


def test_cancelled_appointment_does_not_block_in_filter_when_not_in_occupied() -> None:
    prof_id = uuid4()
    start = datetime(2026, 7, 15, 10, 0, tzinfo=TZ)
    candidate = SlotCandidate(professional_id=prof_id, professional_name="A", start_at=start)
    free = _filter_overlaps([candidate], 30, 10, [])
    assert len(free) == 1


def test_intervals_overlap_with_buffer_zone() -> None:
    a_start = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    a_end = datetime(2026, 7, 15, 10, 40, tzinfo=UTC)
    b_start = datetime(2026, 7, 15, 10, 30, tzinfo=UTC)
    b_end = datetime(2026, 7, 15, 11, 0, tzinfo=UTC)
    assert _intervals_overlap(a_start, a_end, b_start, b_end) is True


def test_same_start_time_warning_true_for_three_slots() -> None:
    start = datetime(2026, 7, 15, 10, 0, tzinfo=TZ)
    slots = [
        AvailableSlot(
            professional_id=uuid4(),
            professional_name="A",
            start=start,
            end=start,
            service_id=uuid4(),
            service_name="Cut",
        )
        for _ in range(3)
    ]
    assert _same_start_time_warning(slots) is True


def test_same_start_time_warning_false_for_single_slot() -> None:
    start = datetime(2026, 7, 15, 10, 0, tzinfo=TZ)
    slots = [
        AvailableSlot(
            professional_id=uuid4(),
            professional_name="A",
            start=start,
            end=start,
            service_id=uuid4(),
            service_name="Cut",
        )
    ]
    assert _same_start_time_warning(slots) is False


# --- Paso 30 §E.1 (motor con BD) -------------------------------------------------


@pytest.fixture
def freeze_slot_now(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_scheduling_now(monkeypatch, "app.services.appointment_slot_service")
    patch_scheduling_now(monkeypatch, "app.services.internal_appointment_service")


async def _slot_env(
    db_session: object,
    tenant_factory: object,
    scheduling_schema_ready: None,
) -> tuple[Tenant, object, object]:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))  # type: ignore[arg-type]
    service_id, professional_id = await seed_scheduling_catalog(db_session, tenant.id)  # type: ignore[arg-type]
    return tenant, service_id, professional_id


def test_normalize_after_skips_occupied_block_with_buffer() -> None:
    prof_id = uuid4()
    after = datetime(2026, 7, 15, 10, 0, tzinfo=TZ)
    occupied = [
        OccupiedBlock(
            professional_id=prof_id,
            start_at=after,
            end_at=datetime(2026, 7, 15, 10, 30, tzinfo=TZ),
        )
    ]
    normalized = _normalize_after(
        after, occupied, prof_id, buffer_minutes=10, granularity_minutes=15
    )
    assert normalized >= datetime(2026, 7, 15, 10, 40, tzinfo=TZ)


def test_generate_candidates_skips_lunch_break() -> None:
    day = date(2026, 7, 15)
    after = datetime(2026, 7, 15, 9, 0, tzinfo=TZ)
    horizon_end = datetime(2026, 7, 15, 23, 0, tzinfo=TZ)
    ranges = [
        HourRange(time(9, 0), time(14, 0)),
        HourRange(time(16, 0), time(18, 0)),
    ]
    starts = _generate_candidates(ranges, day, TZ, after, 15, 30, horizon_end)
    assert all(start.hour < 14 or start.hour >= 16 for start in starts)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_find_slots_respects_center_intersect_professional(
    db_session: object,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_slot_now: None,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))  # type: ignore[arg-type]
    await seed_default_business_hours(db_session, tenant.id)  # type: ignore[arg-type]
    service = await service_catalog_service.create_service(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        SchedulingServiceCreate(name="Corte", duration_minutes=30),
    )
    professional = await professional_service.create_professional(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        ProfessionalCreate(display_name="Estrecho"),
    )
    await professional_service.replace_working_hours(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        professional.id,
        ProfessionalWorkingHoursUpdate(
            slots=[
                ProfessionalWorkingHourSlotUpdate(
                    weekday=2,
                    sort_order=0,
                    opens_at=time(11, 0),
                    closes_at=time(12, 0),
                )
            ]
        ),
    )
    after = datetime(2026, 7, 15, 9, 0, tzinfo=TZ)
    result = await appointment_slot_service.find_next_available_slots(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        FindSlotsRequest(
            service_id=service.id, after=after, professional_id=professional.id, count=5
        ),
    )
    assert result.slots
    assert all(slot.start.hour == 11 for slot in result.slots)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_find_slots_earliest_across_professionals(
    db_session: object,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_slot_now: None,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))  # type: ignore[arg-type]
    await seed_default_business_hours(db_session, tenant.id)  # type: ignore[arg-type]
    service = await service_catalog_service.create_service(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        SchedulingServiceCreate(name="Corte", duration_minutes=30),
    )
    early = await professional_service.create_professional(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        ProfessionalCreate(display_name="Early", sort_order=0),
    )
    late = await professional_service.create_professional(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        ProfessionalCreate(display_name="Late", sort_order=1),
    )
    await professional_service.replace_working_hours(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        late.id,
        ProfessionalWorkingHoursUpdate(
            slots=[
                ProfessionalWorkingHourSlotUpdate(
                    weekday=2,
                    sort_order=0,
                    opens_at=time(12, 0),
                    closes_at=time(13, 0),
                )
            ]
        ),
    )
    after = datetime(2026, 7, 15, 9, 0, tzinfo=TZ)
    result = await appointment_slot_service.find_next_available_slots(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        FindSlotsRequest(service_id=service.id, after=after, count=3),
    )
    assert result.slots[0].professional_id == early.id
    assert result.slots[0].start.hour == 9


@pytest.mark.asyncio
@pytest.mark.integration
async def test_find_slots_returns_both_professionals_at_same_time(
    db_session: object,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_slot_now: None,
) -> None:
    tenant, service_id, _ = await _slot_env(db_session, tenant_factory, scheduling_schema_ready)
    prof_a = await professional_service.create_professional(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        ProfessionalCreate(display_name="A"),
    )
    prof_b = await professional_service.create_professional(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        ProfessionalCreate(display_name="B"),
    )
    target = datetime(2026, 7, 15, 10, 0, tzinfo=TZ)
    result = await appointment_slot_service.find_next_available_slots(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        FindSlotsRequest(service_id=service_id, after=target, count=3),
    )
    same_time = [slot for slot in result.slots if slot.start == target]
    prof_ids = {slot.professional_id for slot in same_time}
    assert prof_a.id in prof_ids and prof_b.id in prof_ids


@pytest.mark.asyncio
@pytest.mark.integration
async def test_find_slots_same_start_time_warning(
    db_session: object,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_slot_now: None,
) -> None:
    tenant, service_id, _ = await _slot_env(db_session, tenant_factory, scheduling_schema_ready)
    for name in ("A", "B", "C"):
        await professional_service.create_professional(
            db_session,  # type: ignore[arg-type]
            tenant.id,
            ProfessionalCreate(display_name=name),
        )
    target = datetime(2026, 7, 15, 10, 0, tzinfo=TZ)
    result = await appointment_slot_service.find_next_available_slots(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        FindSlotsRequest(service_id=service_id, after=target, count=3),
    )
    assert len(result.slots) == 3
    assert result.same_start_time_warning is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_find_slots_excludes_overlapping_booking(
    db_session: object,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_slot_now: None,
) -> None:
    tenant, service_id, professional_id = await _slot_env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    start = future_appointment_start(hour=10, minute=0)
    await internal_appointment_service.create_appointment(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        AppointmentCreate(
            service_id=service_id,
            professional_id=professional_id,
            start_at=start,
            client_name="Ocupado",
            client_phone="600000001",
        ),
    )
    result = await appointment_slot_service.find_next_available_slots(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        FindSlotsRequest(
            service_id=service_id,
            professional_id=professional_id,
            after=start,
            count=1,
        ),
    )
    assert result.slots[0].start > start


@pytest.mark.asyncio
@pytest.mark.integration
async def test_find_slots_ignores_cancelled_and_unassigned(
    db_session: object,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_slot_now: None,
) -> None:

    tenant, service_id, professional_id = await _slot_env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    start = future_appointment_start(hour=10, minute=0)
    assigned = await internal_appointment_service.create_appointment(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        AppointmentCreate(
            service_id=service_id,
            professional_id=professional_id,
            start_at=start,
            client_name="Cancelable",
            client_phone="600000001",
        ),
    )
    await internal_appointment_service.cancel_appointment(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        assigned.id,
        AppointmentCancel(),
    )
    await internal_appointment_service.create_appointment(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        AppointmentCreate(
            service_id=service_id,
            professional_id=None,
            start_at=start,
            client_name="Sin prof",
            client_phone="600000002",
        ),
    )
    result = await appointment_slot_service.find_next_available_slots(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        FindSlotsRequest(
            service_id=service_id,
            professional_id=professional_id,
            after=start,
            count=1,
        ),
    )
    assert result.slots[0].start == start


@pytest.mark.asyncio
@pytest.mark.integration
async def test_find_slots_ignores_past_appointments(
    db_session: object,
    tenant_factory: object,
    scheduling_schema_ready: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, service_id, professional_id = await _slot_env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    past = FIXED_SCHEDULING_NOW.replace(hour=9, minute=0)
    db_session.add(  # type: ignore[attr-defined]
        Appointment(
            tenant_id=tenant.id,
            service_id=service_id,
            professional_id=professional_id,
            start_at=past,
            end_at=past.replace(hour=9, minute=30),
            status=AppointmentStatus.scheduled,
            client_name="Pasada",
            client_phone="600",
            source="manual",
        )
    )
    await db_session.flush()  # type: ignore[attr-defined]
    patch_scheduling_now(monkeypatch, "app.services.appointment_slot_service")
    target = future_appointment_start(hour=10, minute=0)
    result = await appointment_slot_service.find_next_available_slots(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        FindSlotsRequest(
            service_id=service_id,
            professional_id=professional_id,
            after=target,
            count=1,
        ),
    )
    assert result.slots[0].start == target


@pytest.mark.asyncio
@pytest.mark.integration
async def test_find_slots_respects_horizon(
    db_session: object,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_slot_now: None,
) -> None:
    tenant, service_id, professional_id = await _slot_env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    beyond = FIXED_SCHEDULING_NOW + timedelta(days=20)
    result = await appointment_slot_service.find_next_available_slots(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        FindSlotsRequest(
            service_id=service_id,
            professional_id=professional_id,
            after=beyond,
            count=1,
        ),
    )
    assert result.slots == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_find_slots_skips_closed_exception_day(
    db_session: object,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_slot_now: None,
) -> None:
    tenant, service_id, professional_id = await _slot_env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    closed_day = future_appointment_start().date()
    await business_hours_service.create_schedule_exception(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        ScheduleExceptionCreate(exception_date=closed_day, label="Festivo", is_closed=True),
    )
    after = future_appointment_start(hour=9, minute=0)
    result = await appointment_slot_service.find_next_available_slots(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        FindSlotsRequest(
            service_id=service_id,
            professional_id=professional_id,
            after=after,
            count=3,
        ),
    )
    assert all(slot.start.date() != closed_day for slot in result.slots)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_find_slots_rejects_inactive_service(
    db_session: object,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_slot_now: None,
) -> None:

    tenant, service_id, professional_id = await _slot_env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    await service_catalog_service.update_service(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        service_id,
        SchedulingServiceUpdate(is_active=False),
    )
    with pytest.raises(ValidationError, match="inactive"):
        await appointment_slot_service.find_next_available_slots(
            db_session,  # type: ignore[arg-type]
            tenant.id,
            FindSlotsRequest(
                service_id=service_id,
                professional_id=professional_id,
                after=future_appointment_start(),
                count=1,
            ),
        )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_find_slots_without_bookable_professionals(
    db_session: object,
    tenant_factory: object,
    scheduling_schema_ready: None,
    freeze_slot_now: None,
) -> None:
    tenant, service_id, professional_id = await _slot_env(
        db_session, tenant_factory, scheduling_schema_ready
    )
    await professional_service.update_professional(
        db_session,  # type: ignore[arg-type]
        tenant.id,
        professional_id,
        ProfessionalUpdate(is_bookable=False),
    )
    with pytest.raises(ValidationError, match="bookable"):
        await appointment_slot_service.find_next_available_slots(
            db_session,  # type: ignore[arg-type]
            tenant.id,
            FindSlotsRequest(service_id=service_id, after=future_appointment_start(), count=1),
        )
