"""Tests del servicio de facturas (requiere Postgres con migraciones Paso09)."""

from collections.abc import Callable, Coroutine
from typing import Any

import pytest
from app.core.db import set_tenant_context
from app.models import Tenant
from app.services import invoice_service
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_list_invoices_empty(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    invoices = await invoice_service.list_invoices(db_session, tenant.id)
    assert list(invoices) == []
