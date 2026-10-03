"""Tests del semáforo Redis de extracción (TTL tras INCR)."""

from __future__ import annotations

from contextlib import AsyncExitStack
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.jobs.invoice_slots import (
    MAX_PARALLEL_INVOICE_EXTRACTION_PER_TENANT,
    slot_key_for_tenant,
    tenant_invoice_extraction_slot,
)
from arq.worker import Retry

pytestmark = pytest.mark.asyncio


async def test_slot_key_includes_tenant_id() -> None:
    tenant_id = uuid4()
    assert slot_key_for_tenant(tenant_id) == f"invoice:extract:active:{tenant_id}"


async def test_successful_acquire_sets_expire_ttl() -> None:
    tenant_id = uuid4()
    redis = AsyncMock()
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    redis.decr = AsyncMock(return_value=0)

    settings = MagicMock()
    settings.document_extraction_slot_ttl_seconds = 3600

    with patch("app.jobs.invoice_slots.get_settings", return_value=settings):
        async with tenant_invoice_extraction_slot(redis, tenant_id):
            pass

    key = slot_key_for_tenant(tenant_id)
    redis.incr.assert_awaited_once_with(key)
    redis.expire.assert_awaited_once_with(key, 3600)
    redis.decr.assert_awaited_once_with(key)


async def test_slot_full_does_not_expire_and_raises_retry() -> None:
    tenant_id = uuid4()
    redis = AsyncMock()
    redis.incr = AsyncMock(return_value=MAX_PARALLEL_INVOICE_EXTRACTION_PER_TENANT + 1)
    redis.expire = AsyncMock(return_value=True)
    redis.decr = AsyncMock(return_value=MAX_PARALLEL_INVOICE_EXTRACTION_PER_TENANT)

    settings = MagicMock()
    settings.document_extraction_slot_ttl_seconds = 3600

    with (
        patch("app.jobs.invoice_slots.get_settings", return_value=settings),
        pytest.raises(Retry),
    ):
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(tenant_invoice_extraction_slot(redis, tenant_id))

    redis.expire.assert_not_awaited()
    redis.decr.assert_awaited_once()


async def test_zero_ttl_skips_expire() -> None:
    tenant_id = uuid4()
    redis = AsyncMock()
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    redis.decr = AsyncMock(return_value=0)

    settings = MagicMock()
    settings.document_extraction_slot_ttl_seconds = 0

    with patch("app.jobs.invoice_slots.get_settings", return_value=settings):
        async with tenant_invoice_extraction_slot(redis, tenant_id):
            pass

    redis.expire.assert_not_awaited()
    redis.decr.assert_awaited_once()
