"""Helpers compartidos para tests de integración de scheduling (Paso 30)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.core.csrf import CSRF_HEADER_NAME, generate_csrf_token
from app.core.db import set_tenant_context
from app.core.scheduling_defaults import DEFAULT_BUSINESS_HOUR_SEEDS
from app.models import Membership, Tenant, User
from app.schemas.scheduling import (
    BusinessHourSlotUpdate,
    BusinessHoursUpdate,
    ProfessionalCreate,
    SchedulingServiceCreate,
)
from app.services import business_hours_service, professional_service, service_catalog_service
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

SCHEDULING_TZ = ZoneInfo("Europe/Madrid")


def _next_tuesday_morning() -> datetime:
    """Martes 08:00 Europe/Madrid al menos 1 día en el futuro (evita citas 'pasadas')."""
    from datetime import timedelta

    now = datetime.now(SCHEDULING_TZ)
    d = now.date() + timedelta(days=1)
    while d.weekday() != 1:  # Tuesday
        d += timedelta(days=1)
    return datetime(d.year, d.month, d.day, 8, 0, tzinfo=SCHEDULING_TZ)


# Ancla congelada: martes próximo; citas de prueba suelen ir a day_offset=1 (miércoles).
FIXED_SCHEDULING_NOW = _next_tuesday_morning()


def parse_seed_time(value: str) -> time:
    hour, minute = map(int, value.split(":"))
    return time(hour, minute)


async def seed_default_business_hours(db: AsyncSession, tenant_id: UUID) -> None:
    slots = [
        BusinessHourSlotUpdate(
            weekday=seed["weekday"],
            sort_order=seed["sort_order"],
            opens_at=parse_seed_time(seed["opens_at"]),
            closes_at=parse_seed_time(seed["closes_at"]),
        )
        for seed in DEFAULT_BUSINESS_HOUR_SEEDS
    ]
    await business_hours_service.replace_business_hours(
        db,
        tenant_id,
        BusinessHoursUpdate(slots=slots),
    )


async def seed_scheduling_catalog(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    service_name: str = "Consulta",
    professional_name: str = "Dr. Test",
) -> tuple[UUID, UUID]:
    await seed_default_business_hours(db, tenant_id)
    service = await service_catalog_service.create_service(
        db,
        tenant_id,
        SchedulingServiceCreate(name=service_name, duration_minutes=30),
    )
    professional = await professional_service.create_professional(
        db,
        tenant_id,
        ProfessionalCreate(display_name=professional_name),
    )
    return service.id, professional.id


def future_appointment_start(
    *,
    day_offset: int = 1,
    hour: int = 10,
    minute: int = 0,
) -> datetime:
    """Slot futuro alineado a granularidad 15 min (respecto a FIXED_SCHEDULING_NOW)."""
    base = FIXED_SCHEDULING_NOW.date()
    from datetime import timedelta

    target = base + timedelta(days=day_offset)
    return datetime(target.year, target.month, target.day, hour, minute, tzinfo=SCHEDULING_TZ)


def patch_scheduling_now(monkeypatch: Any, module_path: str) -> None:
    """Fija datetime.now() y display_today en un módulo (evita citas 'pasadas')."""

    import datetime as dt_module

    real_datetime = dt_module.datetime

    class FixedDatetime(real_datetime):
        @classmethod
        def now(cls, tz: dt_module.tzinfo | None = None) -> real_datetime:
            if tz is not None:
                return FIXED_SCHEDULING_NOW.astimezone(tz)
            return FIXED_SCHEDULING_NOW

    monkeypatch.setattr(f"{module_path}.datetime", FixedDatetime)
    # _is_past_day usa display_today (reloj real), no datetime.now.
    monkeypatch.setattr(
        f"{module_path}.display_today",
        lambda timezone=None: FIXED_SCHEDULING_NOW.date(),
        raising=False,
    )


@dataclass(frozen=True, slots=True)
class CommittedSchedulingTenant:
    tenant_id: UUID
    user_id: UUID
    tenant: Tenant
    user: User
    membership: Membership
    service_id: UUID
    professional_id: UUID


async def seed_committed_scheduling_tenant(
    rls_database_url: str,
    *,
    permissions: dict[str, dict[str, bool]] | None = None,
    role: str = "admin",
) -> CommittedSchedulingTenant:
    """Inserta tenant + catálogo scheduling en BD con commit (visible desde HTTP)."""
    suffix = uuid4().hex[:8]
    engine = create_async_engine(rls_database_url, poolclass=NullPool)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        tenant = Tenant(
            clerk_org_id=f"org_sched_{suffix}",
            name=f"Scheduling Tenant {suffix}",
            plan="free",
            settings={},
        )
        user = User(
            clerk_user_id=f"user_sched_{suffix}",
            email=f"sched_{suffix}@test.local",
            name="Sched User",
        )
        session.add(tenant)
        session.add(user)
        await session.flush()
        await set_tenant_context(session, str(tenant.id))
        perms = permissions or {
            "appointments": {"view": True, "create": True, "edit": True, "cancel": True},
        }
        membership = Membership(
            user_id=user.id,
            tenant_id=tenant.id,
            role=role,
            permissions=perms,
        )
        session.add(membership)
        service_id, professional_id = await seed_scheduling_catalog(session, tenant.id)
        await session.commit()
        result = CommittedSchedulingTenant(
            tenant_id=tenant.id,
            user_id=user.id,
            tenant=tenant,
            user=user,
            membership=membership,
            service_id=service_id,
            professional_id=professional_id,
        )
    await engine.dispose()
    return result


def fake_clerk_resolve_factory(
    tenant: Tenant,
    user: User,
    membership: Membership,
) -> Any:
    async def _resolve(request: Any) -> None:
        request.state.tenant = tenant
        request.state.user = user
        request.state.membership = membership
        request.state.is_superadmin = False
        request.state.force_password_reset = False

    return _resolve


def csrf_headers(user: User, tenant: Tenant) -> dict[str, str]:
    return {CSRF_HEADER_NAME: generate_csrf_token(user_id=user.id, tenant_id=tenant.id)}


def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer fake-jwt", "Accept": "text/html"}
