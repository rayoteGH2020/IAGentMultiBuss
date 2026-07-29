"""Tests del tenant auto-provisionado para evals de extracción."""

from __future__ import annotations

import uuid

import pytest
from app.evals.eval_tenant import EVAL_TENANT_ID, ensure_eval_tenant
from app.models import Tenant
from sqlalchemy import select


@pytest.mark.asyncio
async def test_ensure_eval_tenant_creates_fixed_tenant(db_session) -> None:
    tenant_id = await ensure_eval_tenant(None)
    assert tenant_id == EVAL_TENANT_ID
    row = await db_session.execute(select(Tenant).where(Tenant.id == EVAL_TENANT_ID))
    assert row.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_ensure_eval_tenant_validates_existing(db_session, tenant_factory) -> None:
    tenant = await tenant_factory()
    await db_session.commit()
    resolved = await ensure_eval_tenant(tenant.id)
    assert resolved == tenant.id


@pytest.mark.asyncio
async def test_ensure_eval_tenant_rejects_unknown() -> None:
    unknown = uuid.uuid4()
    with pytest.raises(SystemExit, match="No existe tenant"):
        await ensure_eval_tenant(unknown)
