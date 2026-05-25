"""Tests de resolución de doc_type_code contra catálogo BD."""

from __future__ import annotations

import pytest
from app.core.db import set_tenant_context
from app.core.errors import ValidationError
from app.models import DocTypeCode, Tenant
from app.services import doc_type_service

pytestmark = pytest.mark.asyncio


async def test_resolve_active_doc_type_normalizes_code(
    invoices_schema_ready: None,
    db_session,
) -> None:
    tenant = Tenant(name="DocType resolve tenant")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))

    doc_type = await doc_type_service.resolve_active_doc_type(db_session, "  FACTURA  ")
    assert doc_type.code == DocTypeCode.factura.value
    assert doc_type.is_active is True


async def test_resolve_active_doc_type_unknown_raises(
    invoices_schema_ready: None,
    db_session,
    tenant_factory,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    with pytest.raises(ValidationError, match="Unknown or inactive"):
        await doc_type_service.resolve_active_doc_type(db_session, "contrato_xyz")


async def test_list_doc_type_codes_includes_mvp_types(
    invoices_schema_ready: None,
    db_session,
    tenant_factory,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    codes = await doc_type_service.list_doc_type_codes(db_session)
    assert DocTypeCode.factura.value in codes
    assert DocTypeCode.ticket.value in codes
