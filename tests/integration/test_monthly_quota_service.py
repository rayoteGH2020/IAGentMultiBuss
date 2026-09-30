"""Cupos mensuales en PostgreSQL (D027): consumo atómico, bolsa, devoluciones y ampliación."""

from __future__ import annotations

import asyncio
import os
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from app.core.db import set_tenant_context
from app.core.entitlement_codes import (
    LIMIT_CHAT_QUESTIONS_PER_MONTH,
    LIMIT_INVOICES_PER_MONTH,
    LIMIT_TICKETS_PER_MONTH,
    MONTHLY_QUOTA_CODES,
)
from app.core.errors import ValidationError
from app.models import AuditLog, Tenant
from app.schemas.entitlements import Entitlements
from app.services import monthly_quota_service
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.db_target import resolve_test_urls

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

PERIOD = date(2026, 10, 1)


@pytest.fixture
async def quota_schema_ready(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'quota_usage'"
        )
    )
    if result.scalar_one_or_none() is None:
        pytest.skip("Run migration p77_quota_usage_01 (`alembic upgrade head`).")


def _ents(**limits: int | None) -> Entitlements:
    values: dict[str, Decimal | None] = {code: Decimal("0") for code in MONTHLY_QUOTA_CODES}
    values.update({code: None if v is None else Decimal(v) for code, v in limits.items()})
    return Entitlements(plan_code="basic", features=frozenset(), limits=values)


async def _tenant(db: AsyncSession, tenant_factory) -> Tenant:  # type: ignore[no-untyped-def]
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db, str(tenant.id))
    return tenant


async def test_consume_until_cap_then_refuses(
    quota_schema_ready: None, db_session: AsyncSession, tenant_factory
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    ents = _ents(**{LIMIT_CHAT_QUESTIONS_PER_MONTH: 2})

    for _ in range(2):
        assert await monthly_quota_service.try_consume(
            db_session, ents, tenant.id, LIMIT_CHAT_QUESTIONS_PER_MONTH, period=PERIOD
        )
    assert not await monthly_quota_service.try_consume(
        db_session, ents, tenant.id, LIMIT_CHAT_QUESTIONS_PER_MONTH, period=PERIOD
    )
    usage = await monthly_quota_service.get_usage(db_session, ents, tenant.id, period=PERIOD)
    assert usage[LIMIT_CHAT_QUESTIONS_PER_MONTH].used == 2


async def test_invoices_and_tickets_share_the_bag(
    quota_schema_ready: None, db_session: AsyncSession, tenant_factory
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    ents = _ents(**{LIMIT_INVOICES_PER_MONTH: 2, LIMIT_TICKETS_PER_MONTH: 1})

    # 3 facturas caben: la bolsa es 2 + 1 aunque el tope de facturas sea 2.
    for _ in range(3):
        assert await monthly_quota_service.try_consume(
            db_session, ents, tenant.id, LIMIT_INVOICES_PER_MONTH, period=PERIOD
        )
    assert not await monthly_quota_service.try_consume(
        db_session, ents, tenant.id, LIMIT_TICKETS_PER_MONTH, period=PERIOD
    )


async def test_unlimited_member_makes_bag_unlimited(
    quota_schema_ready: None, db_session: AsyncSession, tenant_factory
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    ents = _ents(**{LIMIT_INVOICES_PER_MONTH: None, LIMIT_TICKETS_PER_MONTH: 0})

    assert await monthly_quota_service.try_consume(
        db_session, ents, tenant.id, LIMIT_TICKETS_PER_MONTH, delta=50, period=PERIOD
    )


async def test_zero_cap_blocks(
    quota_schema_ready: None, db_session: AsyncSession, tenant_factory
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    assert not await monthly_quota_service.try_consume(
        db_session, _ents(), tenant.id, LIMIT_CHAT_QUESTIONS_PER_MONTH, period=PERIOD
    )


async def test_periods_are_independent(
    quota_schema_ready: None, db_session: AsyncSession, tenant_factory
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    ents = _ents(**{LIMIT_CHAT_QUESTIONS_PER_MONTH: 1})

    assert await monthly_quota_service.try_consume(
        db_session, ents, tenant.id, LIMIT_CHAT_QUESTIONS_PER_MONTH, period=PERIOD
    )
    assert await monthly_quota_service.try_consume(
        db_session, ents, tenant.id, LIMIT_CHAT_QUESTIONS_PER_MONTH, period=date(2026, 11, 1)
    )


async def test_release_returns_quota_and_never_goes_negative(
    quota_schema_ready: None, db_session: AsyncSession, tenant_factory
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    ents = _ents(**{LIMIT_CHAT_QUESTIONS_PER_MONTH: 1})
    code = LIMIT_CHAT_QUESTIONS_PER_MONTH

    assert await monthly_quota_service.try_consume(db_session, ents, tenant.id, code, period=PERIOD)
    await monthly_quota_service.release(db_session, tenant.id, code, period=PERIOD, delta=5)
    usage = await monthly_quota_service.get_usage(db_session, ents, tenant.id, period=PERIOD)
    assert usage[code].used == 0
    assert await monthly_quota_service.try_consume(db_session, ents, tenant.id, code, period=PERIOD)


async def test_add_extra_only_for_current_period_and_audited(
    quota_schema_ready: None, db_session: AsyncSession, tenant_factory
) -> None:
    from app.core.billing_period import current_period_start

    tenant = await _tenant(db_session, tenant_factory)
    ents = _ents(**{LIMIT_CHAT_QUESTIONS_PER_MONTH: 1})
    code = LIMIT_CHAT_QUESTIONS_PER_MONTH
    current = current_period_start()

    assert (
        await monthly_quota_service.add_extra(
            db_session,
            tenant_id=tenant.id,
            code=code,
            amount=2,
            actor_user_id=None,
            reason="piloto",
        )
        == 2
    )
    assert (
        await monthly_quota_service.add_extra(
            db_session, tenant_id=tenant.id, code=code, amount=1, actor_user_id=None
        )
        == 3
    )

    usage = await monthly_quota_service.get_usage(db_session, ents, tenant.id)
    assert usage[code].extra == 3
    assert usage[code].cap == 4
    for _ in range(4):
        assert await monthly_quota_service.try_consume(db_session, ents, tenant.id, code)
    assert not await monthly_quota_service.try_consume(db_session, ents, tenant.id, code)

    other = date(2027, 1, 1) if current != date(2027, 1, 1) else date(2027, 2, 1)
    other_usage = await monthly_quota_service.get_usage(db_session, ents, tenant.id, period=other)
    assert other_usage[code].extra == 0

    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant.id,
                    AuditLog.action == monthly_quota_service.ACTION_QUOTA_EXTRA_ADDED,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 2


@pytest.mark.parametrize("amount", [0, -1, monthly_quota_service.MAX_EXTRA_PER_REQUEST + 1])
async def test_add_extra_rejects_out_of_range(
    quota_schema_ready: None, db_session: AsyncSession, tenant_factory, amount: int
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    with pytest.raises(ValidationError):
        await monthly_quota_service.add_extra(
            db_session,
            tenant_id=tenant.id,
            code=LIMIT_CHAT_QUESTIONS_PER_MONTH,
            amount=amount,
            actor_user_id=None,
        )


async def test_unknown_code_is_rejected(
    quota_schema_ready: None, db_session: AsyncSession, tenant_factory
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    with pytest.raises(ValidationError):
        await monthly_quota_service.try_consume(
            db_session, _ents(), tenant.id, "documents_per_day", period=PERIOD
        )


async def test_rls_hides_other_tenant_usage(
    quota_schema_ready: None, db_session: AsyncSession, tenant_factory
) -> None:
    ents = _ents(**{LIMIT_CHAT_QUESTIONS_PER_MONTH: 5})
    tenant_a = await _tenant(db_session, tenant_factory)
    assert await monthly_quota_service.try_consume(
        db_session, ents, tenant_a.id, LIMIT_CHAT_QUESTIONS_PER_MONTH, period=PERIOD
    )
    tenant_b: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant_b.id))
    rows = await db_session.execute(
        text("SELECT count(*) FROM quota_usage WHERE tenant_id = :t"), {"t": tenant_a.id}
    )
    assert rows.scalar_one() == 0


async def test_concurrent_consumers_cannot_exceed_cap(quota_schema_ready: None) -> None:
    """Dos transacciones a la vez por el último hueco: solo una lo consigue."""
    admin_url, rls_url = resolve_test_urls(os.environ)
    admin_engine = create_async_engine(admin_url, poolclass=NullPool)
    rls_engine = create_async_engine(rls_url, poolclass=NullPool)
    admin_sm = async_sessionmaker(admin_engine, expire_on_commit=False)
    rls_sm = async_sessionmaker(rls_engine, expire_on_commit=False)
    tenant_id: UUID | None = None
    ents = _ents(**{LIMIT_CHAT_QUESTIONS_PER_MONTH: 1})
    try:
        async with admin_sm() as db:
            tenant = Tenant(name=f"Quota race {uuid4().hex[:8]}", plan="basic", plan_code="basic")
            db.add(tenant)
            await db.commit()
            tenant_id = tenant.id

        async def consume(hold_seconds: float) -> bool:
            assert tenant_id is not None
            async with rls_sm() as db:
                await set_tenant_context(db, str(tenant_id))
                ok = await monthly_quota_service.try_consume(
                    db, ents, tenant_id, LIMIT_CHAT_QUESTIONS_PER_MONTH, period=PERIOD
                )
                # Mantiene el bloqueo abierto para forzar que el otro espere.
                await asyncio.sleep(hold_seconds)
                await db.commit()
                return ok

        results = await asyncio.gather(consume(0.3), consume(0.3))
        assert sorted(results) == [False, True]
    finally:
        if tenant_id is not None:
            async with admin_sm() as db:
                await db.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant_id})
                await db.commit()
        await admin_engine.dispose()
        await rls_engine.dispose()
