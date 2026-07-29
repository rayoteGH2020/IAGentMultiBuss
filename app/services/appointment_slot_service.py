"""Motor de huecos disponibles (Paso 30 Fase B)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.datetime_display import resolve_display_timezone
from app.core.errors import NotFoundError, ValidationError
from app.models.appointment import Appointment
from app.models.business_hour import BusinessHour
from app.models.professional_working_hour import ProfessionalWorkingHour
from app.schemas.scheduling import (
    AppointmentStatus,
    AvailableSlot,
    FindSlotsRequest,
    FindSlotsResponse,
)
from app.services import business_hours_service, professional_service, service_catalog_service


@dataclass(frozen=True, slots=True)
class HourRange:
    opens_at: time
    closes_at: time


@dataclass(frozen=True, slots=True)
class OccupiedBlock:
    professional_id: UUID
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True, slots=True)
class SlotCandidate:
    professional_id: UUID
    professional_name: str
    start_at: datetime


def _combine(day: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, t.hour, t.minute, t.second, tzinfo=tz)


def _intersect_time_ranges(a: HourRange, b: HourRange) -> HourRange | None:
    start = max(a.opens_at, b.opens_at)
    end = min(a.closes_at, b.closes_at)
    if start >= end:
        return None
    return HourRange(opens_at=start, closes_at=end)


def _intersect_hours(
    center_slots: list[HourRange],
    prof_slots: list[HourRange],
) -> list[HourRange]:
    ranges: list[HourRange] = []
    for center in center_slots:
        for prof in prof_slots:
            hit = _intersect_time_ranges(center, prof)
            if hit is not None:
                ranges.append(hit)
    return ranges


def _round_up_to_granularity(dt: datetime, granularity_minutes: int) -> datetime:
    if granularity_minutes <= 0:
        return dt
    epoch = int(dt.timestamp())
    step = granularity_minutes * 60
    rounded = ((epoch + step - 1) // step) * step
    return datetime.fromtimestamp(rounded, tz=dt.tzinfo)


def _effective_end(end_at: datetime, buffer_minutes: int) -> datetime:
    return end_at + timedelta(minutes=buffer_minutes)


def _intervals_overlap(
    start_a: datetime,
    end_a: datetime,
    start_b: datetime,
    end_b: datetime,
) -> bool:
    return start_a < end_b and start_b < end_a


def _generate_candidates(
    ranges: list[HourRange],
    day: date,
    tz: ZoneInfo,
    after: datetime,
    granularity_minutes: int,
    duration_minutes: int,
    horizon_end: datetime,
) -> list[datetime]:
    candidates: list[datetime] = []
    duration_delta = timedelta(minutes=duration_minutes)
    for slot_range in ranges:
        cursor = _combine(day, slot_range.opens_at, tz)
        range_end = _combine(day, slot_range.closes_at, tz)
        if cursor < after:
            cursor = _round_up_to_granularity(after, granularity_minutes)
            if cursor < after:
                cursor += timedelta(minutes=granularity_minutes)
        while cursor + duration_delta <= range_end and cursor <= horizon_end:
            if cursor >= after:
                candidates.append(cursor)
            cursor += timedelta(minutes=granularity_minutes)
    return candidates


def _filter_overlaps(
    candidates: list[SlotCandidate],
    duration_minutes: int,
    buffer_minutes: int,
    occupied: list[OccupiedBlock],
) -> list[SlotCandidate]:
    free: list[SlotCandidate] = []
    duration_delta = timedelta(minutes=duration_minutes)
    for candidate in candidates:
        cand_start = candidate.start_at
        cand_end = _effective_end(cand_start + duration_delta, buffer_minutes)
        blocked = False
        for block in occupied:
            if block.professional_id != candidate.professional_id:
                continue
            block_end = _effective_end(block.end_at, buffer_minutes)
            if _intervals_overlap(cand_start, cand_end, block.start_at, block_end):
                blocked = True
                break
        if not blocked:
            free.append(candidate)
    return free


def _normalize_after(
    after: datetime,
    occupied: list[OccupiedBlock],
    professional_id: UUID | None,
    buffer_minutes: int,
    granularity_minutes: int,
) -> datetime:
    cursor = after
    changed = True
    while changed:
        changed = False
        for block in occupied:
            if professional_id is not None and block.professional_id != professional_id:
                continue
            block_end = _effective_end(block.end_at, buffer_minutes)
            if block.start_at <= cursor < block_end:
                cursor = _round_up_to_granularity(block_end, granularity_minutes)
                if cursor < block_end:
                    cursor += timedelta(minutes=granularity_minutes)
                changed = True
    return cursor


def _slots_to_hour_ranges(
    rows: Sequence[BusinessHour | ProfessionalWorkingHour],
    weekday: int,
) -> list[HourRange]:
    ranges: list[HourRange] = []
    for row in rows:
        if row.weekday != weekday:
            continue
        if row.opens_at is None or row.closes_at is None:
            continue
        ranges.append(HourRange(opens_at=row.opens_at, closes_at=row.closes_at))
    return ranges


def _same_start_time_warning(slots: list[AvailableSlot]) -> bool:
    if len(slots) < 2:
        return False
    first = slots[0].start
    return all(slot.start == first for slot in slots)


async def find_next_available_slots(
    db: AsyncSession,
    tenant_id: UUID,
    request: FindSlotsRequest,
) -> FindSlotsResponse:
    service = await service_catalog_service.require_active_service(
        db, tenant_id, request.service_id
    )
    settings = await business_hours_service.get_scheduling_settings(db, tenant_id)
    tz = resolve_display_timezone(settings.timezone)
    now = datetime.now(tz)
    horizon_end = now + timedelta(days=settings.search_horizon_days)
    duration = service.duration_minutes
    granularity = settings.slot_granularity_minutes
    buffer_minutes = settings.buffer_minutes

    if request.professional_id is not None:
        prof = await professional_service.get_professional(db, tenant_id, request.professional_id)
        if not prof.is_active or not prof.is_bookable:
            raise NotFoundError("Professional not available for booking")
        professionals = [prof]
    else:
        professionals = await professional_service.list_bookable_professionals(db, tenant_id)
        if not professionals:
            raise ValidationError("No bookable professionals configured")

    center_result = await db.execute(
        select(BusinessHour).where(BusinessHour.tenant_id == tenant_id)
    )
    center_hours = list(center_result.scalars().all())
    if not center_hours:
        raise ValidationError("Business hours not configured")

    closed_dates = await business_hours_service.get_closed_dates(
        db,
        tenant_id,
        now.date(),
        horizon_end.date(),
    )

    occupied_result = await db.execute(
        select(Appointment).where(
            Appointment.tenant_id == tenant_id,
            Appointment.professional_id.is_not(None),
            Appointment.status != AppointmentStatus.cancelled,
            Appointment.start_at >= now.astimezone(UTC),
        )
    )
    occupied = [
        OccupiedBlock(
            professional_id=row.professional_id,
            start_at=row.start_at,
            end_at=row.end_at,
        )
        for row in occupied_result.scalars().all()
        if row.professional_id is not None
    ]

    after = _normalize_after(
        request.after.astimezone(tz),
        occupied,
        request.professional_id,
        buffer_minutes,
        granularity,
    )

    all_candidates: list[SlotCandidate] = []
    day_cursor = after.date()
    end_date = horizon_end.date()

    while day_cursor <= end_date:
        if day_cursor in closed_dates:
            day_cursor += timedelta(days=1)
            continue

        weekday = day_cursor.weekday()
        center_ranges = _slots_to_hour_ranges(center_hours, weekday)
        if not center_ranges:
            day_cursor += timedelta(days=1)
            continue

        for prof in professionals:
            wh_result = await db.execute(
                select(ProfessionalWorkingHour).where(
                    ProfessionalWorkingHour.tenant_id == tenant_id,
                    ProfessionalWorkingHour.professional_id == prof.id,
                )
            )
            prof_ranges = _intersect_hours(
                center_ranges,
                _slots_to_hour_ranges(list(wh_result.scalars().all()), weekday),
            )
            if not prof_ranges:
                continue

            day_after = (
                after if day_cursor == after.date() else _combine(day_cursor, time(0, 0), tz)
            )
            starts = _generate_candidates(
                prof_ranges,
                day_cursor,
                tz,
                day_after,
                granularity,
                duration,
                horizon_end,
            )
            for start_at in starts:
                all_candidates.append(
                    SlotCandidate(
                        professional_id=prof.id,
                        professional_name=prof.display_name,
                        start_at=start_at,
                    )
                )

        day_cursor += timedelta(days=1)

    free_candidates = _filter_overlaps(
        all_candidates,
        duration,
        buffer_minutes,
        occupied,
    )
    free_candidates.sort(key=lambda c: (c.start_at, str(c.professional_id)))

    selected: list[AvailableSlot] = []
    for candidate in free_candidates:
        if len(selected) >= request.count:
            break
        if selected and candidate.start_at > selected[0].start:
            break
        selected.append(
            AvailableSlot(
                professional_id=candidate.professional_id,
                professional_name=candidate.professional_name,
                start=candidate.start_at,
                end=candidate.start_at + timedelta(minutes=duration),
                service_id=service.id,
                service_name=service.name,
            )
        )

    return FindSlotsResponse(
        slots=selected,
        same_start_time_warning=_same_start_time_warning(selected),
    )
