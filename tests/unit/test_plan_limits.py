"""Cuotas por plan (Paso04): resolucion, rate limiter y presupuesto LLM."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.entitlement_codes import (
    LIMIT_CHAT_MESSAGES_PER_DAY,
    LIMIT_DOCUMENTS_PER_DAY,
    LIMIT_LLM_BUDGET_EUR_MONTH,
)
from app.core.errors import RateLimitError
from app.core.plan_limits import resolve_budget_cap, resolve_quota_cap
from app.core.rate_limiter import (
    assert_quota_headroom,
    check_documents_upload_rate,
    increment_quota,
    record_quota_usage,
)
from app.schemas.entitlements import Entitlements
from app.services import plan_quota_service


def _ents(**limits: Decimal | None) -> Entitlements:
    from app.core.entitlement_codes import LIMIT_CODES

    all_limits: dict[str, Decimal | None] = {code: Decimal("0") for code in LIMIT_CODES}
    all_limits.update(limits)
    return Entitlements(
        plan_code="basic",
        features=frozenset({"documents", "documents_chat"}),
        limits=all_limits,
    )


def test_resolve_quota_cap_unlimited_when_null() -> None:
    ents = _ents(**{LIMIT_DOCUMENTS_PER_DAY: None})
    assert resolve_quota_cap(ents, LIMIT_DOCUMENTS_PER_DAY) is None


def test_resolve_quota_cap_platform_min_when_both_set() -> None:
    ents = _ents(**{LIMIT_CHAT_MESSAGES_PER_DAY: Decimal("80")})
    assert resolve_quota_cap(ents, LIMIT_CHAT_MESSAGES_PER_DAY, platform_cap=60) == 60


def test_resolve_budget_cap_total_unlimited() -> None:
    ents = _ents(**{LIMIT_LLM_BUDGET_EUR_MONTH: None})
    assert resolve_budget_cap(ents, LIMIT_LLM_BUDGET_EUR_MONTH) is None


@pytest.mark.asyncio
async def test_increment_quota_unlimited_noop() -> None:
    redis = AsyncMock()
    await increment_quota(
        redis,
        key="rate:test",
        delta=1,
        max_count=None,
        ttl_seconds=3600,
        error_message="nope",
        log_event="test",
    )
    redis.incrby.assert_not_awaited()


@pytest.mark.asyncio
async def test_increment_quota_raises_and_reverts() -> None:
    redis = AsyncMock()
    redis.incrby = AsyncMock(return_value=2)
    redis.expire = AsyncMock()
    redis.decrby = AsyncMock()

    with pytest.raises(RateLimitError, match="documentos"):
        await check_documents_upload_rate(
            redis,
            tenant_id=uuid4(),
            max_per_day=1,
            n_files=1,
        )

    redis.decrby.assert_awaited_once()


@pytest.mark.asyncio
async def test_assert_headroom_blocks_without_increment() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b"1")

    with pytest.raises(RateLimitError):
        await assert_quota_headroom(
            redis,
            key="rate:retries",
            delta=1,
            max_count=1,
            error_message="retry cap",
            log_event="test",
        )

    redis.incrby.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_quota_usage_increments() -> None:
    redis = AsyncMock()
    redis.incrby = AsyncMock(return_value=1)
    redis.expire = AsyncMock()

    await record_quota_usage(redis, key="rate:retries", delta=1, ttl_seconds=86400)

    redis.incrby.assert_awaited_once_with("rate:retries", 1)


@pytest.mark.asyncio
async def test_ensure_llm_budget_blocks_when_spent(
    usage_meter_schema_ready: None,
    db_session,
    tenant_factory,
) -> None:
    from app.core.db import set_tenant_context
    from app.services import usage_meter_service

    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    period = usage_meter_service.current_billing_period()
    await usage_meter_service.add_llm_cost_eur(
        db_session,
        tenant_id=tenant.id,
        delta=Decimal("10"),
        period=period,
    )
    await db_session.flush()

    ents = Entitlements(
        plan_code="basic",
        features=frozenset(),
        limits={LIMIT_LLM_BUDGET_EUR_MONTH: Decimal("5")},
    )

    with pytest.raises(RateLimitError, match="presupuesto"):
        await plan_quota_service.ensure_llm_budget(db_session, ents, tenant.id)
