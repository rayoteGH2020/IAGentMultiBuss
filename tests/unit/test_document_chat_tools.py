"""Tests de ejecutores de tools documentales con sesión de BD."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.core.errors import ValidationError
from app.llm.tools.document_chat import (
    ListDocTypesArgs,
    SearchDocumentsArgs,
    execute_list_doc_types,
    execute_search_documents,
)
from app.llm.tools.registry import ToolContext
from app.models import DocTypeCode, Tenant
from app.schemas.document_query import DocumentSearchFilters
from app.schemas.invoice import Factura, LineaFactura
from app.services import invoice_service


async def _seed_invoice(
    db,
    tenant_id,
    *,
    proveedor: str,
    total: Decimal,
) -> None:
    inv = await invoice_service.create_invoice_stub(
        db,
        tenant_id,
        source_file_key=f"test/{uuid4().hex}.pdf",
        source_filename="inv.pdf",
        source_mime="application/pdf",
    )
    inv = await invoice_service.get_invoice(db, tenant_id, inv.id)
    factura = Factura(
        fecha=date(2025, 4, 10),
        proveedor=proveedor,
        cif_nif="B-TEST-001",  # pragma: allowlist secret
        base_imponible=total,
        iva_percent=Decimal("21"),
        iva_amount=Decimal("0"),
        total=total,
        lineas=[
            LineaFactura(
                descripcion="x",
                cantidad=Decimal("1"),
                precio_unitario=total,
                total=total,
            ),
        ],
        confidence=0.9,
    )
    await invoice_service.apply_extraction_result(
        db,
        invoice=inv,
        factura=factura,
        llm_call_id=uuid4(),
    )


async def test_execute_list_doc_types_returns_catalog(
    invoices_schema_ready: None,
    db_session,
    tenant_factory,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    ctx = ToolContext(db=db_session, tenant_id=tenant.id)

    result = await execute_list_doc_types(ctx, ListDocTypesArgs())

    assert result.ok is True
    data = cast(dict[str, Any], result.data)
    codes = {row["code"] for row in data["doc_types"]}
    assert DocTypeCode.factura.value in codes
    assert DocTypeCode.ticket.value in codes


@pytest.mark.asyncio
async def test_execute_search_documents_respects_tenant_rls(
    invoices_schema_ready: None,
    db_session,
    tenant_factory,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    await _seed_invoice(
        db_session,
        tenant.id,
        proveedor="Proveedor Alpha",
        total=Decimal("99.00"),
    )
    await db_session.commit()
    await set_tenant_context(db_session, str(tenant.id))

    ctx = ToolContext(db=db_session, tenant_id=tenant.id)
    result = await execute_search_documents(
        ctx,
        SearchDocumentsArgs(
            doc_type_code=DocTypeCode.factura.value,
            proveedor_query="alpha",
            limit=10,
        ),
    )

    assert result.ok is True
    assert result.data["total"] == 1
    assert len(result.citations) == 1


@pytest.mark.asyncio
async def test_execute_search_documents_invalid_doc_type(
    invoices_schema_ready: None,
    db_session,
    tenant_factory,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    ctx = ToolContext(db=db_session, tenant_id=tenant.id)

    with pytest.raises(ValidationError):
        await execute_search_documents(
            ctx,
            SearchDocumentsArgs(doc_type_code="albaran_inexistente", limit=5),
        )


def test_document_search_filters_rejects_invalid_limit() -> None:
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        DocumentSearchFilters(limit=0)
