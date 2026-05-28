"""Integración RLS: tools documentales no cruzan tenants."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from app.core.db import set_tenant_context
from app.llm.tools.document_chat import SearchDocumentsArgs, execute_search_documents
from app.llm.tools.registry import ToolContext
from app.models import DocTypeCode, Tenant
from app.schemas.invoice import Factura, LineaFactura
from app.services import invoice_service

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _seed_invoice_for_tenant(db: object, tenant_id: UUID) -> None:
    inv = await invoice_service.create_invoice_stub(
        db,
        tenant_id,
        source_file_key=f"test/{uuid4().hex}.pdf",
        source_filename="secret.pdf",
        source_mime="application/pdf",
    )
    inv = await invoice_service.get_invoice(db, tenant_id, inv.id)
    factura = Factura(
        fecha=date(2025, 6, 1),
        proveedor="Tenant Secret Corp",
        cif_nif="ATEST00001",  # pragma: allowlist secret
        base_imponible=Decimal("100"),
        iva_percent=Decimal("21"),
        iva_amount=Decimal("21"),
        total=Decimal("121"),
        lineas=[
            LineaFactura(
                descripcion="item",
                cantidad=Decimal("1"),
                precio_unitario=Decimal("100"),
                total=Decimal("100"),
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


@pytest.mark.asyncio
async def test_search_documents_other_tenant_sees_zero(
    invoices_schema_ready: None,
    db_session,
) -> None:
    tenant_a = Tenant(name="Tenant A RLS")
    tenant_b = Tenant(name="Tenant B RLS")
    db_session.add_all([tenant_a, tenant_b])
    await db_session.flush()

    await set_tenant_context(db_session, str(tenant_a.id))
    await _seed_invoice_for_tenant(db_session, tenant_a.id)
    await db_session.commit()

    await set_tenant_context(db_session, str(tenant_b.id))
    ctx_b = ToolContext(db=db_session, tenant_id=tenant_b.id)
    result = await execute_search_documents(
        ctx_b,
        SearchDocumentsArgs(
            doc_type_code=DocTypeCode.factura.value,
            proveedor_query="secret",
            limit=50,
        ),
    )

    assert result.ok is True
    assert result.data["total"] == 0
    assert result.data["items"] == []
