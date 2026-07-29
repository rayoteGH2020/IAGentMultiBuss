"""Tests del servicio de facturas (requiere Postgres con migraciones Paso09).

A pesar de estar en tests/unit/, este test usa una BD real (via conftest.py)
porque el servicio ejercita SQLAlchemy y RLS. En Agents.md §9 se clasifica como
integration; el directorio 'unit/' agrupa tests de servicio que no levantan HTTP.
"""

from collections.abc import Callable, Coroutine
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.models import DocTypeCode, Invoice, InvoiceStatus, Tenant
from app.schemas.invoice import DesgloseIVA, Factura
from app.services import doc_type_service, invoice_service
from sqlalchemy.ext.asyncio import AsyncSession

# pytest.mark.asyncio: habilita pytest-asyncio para funciones async en este módulo.
# pytest.mark.integration: permite filtrar con `-m integration` y excluirlos en CI rápido.
pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_list_invoices_empty(
    # invoices_schema_ready: fixture de efecto secundario que garantiza que las
    # migraciones de invoices están aplicadas antes de ejecutar el test. El
    # tipo None indica que no devuelve valor útil, solo ejecuta el setup.
    invoices_schema_ready: None,
    # db_session: sesión async de Postgres definida en conftest.py. Cada test
    # obtiene una sesión limpia (con rollback al final para no contaminar otros).
    db_session: AsyncSession,
    # tenant_factory: callable que crea un Tenant en BD con UUIDs únicos.
    # Usar un factory (no un objeto fijo) garantiza aislamiento entre tests:
    # cada test trabaja con su propio tenant y no comparte datos.
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    # RLS requiere que la variable de sesión esté seteada antes de cualquier query.
    # En producción lo hace el middleware de auth; en tests hay que hacerlo manualmente
    # porque no existe ciclo de vida de request FastAPI.
    await set_tenant_context(db_session, str(tenant.id))
    invoices = await invoice_service.list_invoices(db_session, tenant.id)
    # list(): list_invoices devuelve Sequence[Invoice] (puede ser un ScalarResult);
    # compararlo directamente con [] fallaría. list() lo convierte a lista Python.
    assert list(invoices) == []


async def test_get_invoice_date_stats_by_fecha(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    monkeypatch.setattr(
        invoice_service,
        "display_today",
        lambda timezone=None: date(2026, 5, 20),
    )

    async def add_invoice(fecha: date | None) -> None:
        doc_type_id = await doc_type_service.get_doc_type_id(db_session, DocTypeCode.factura)
        invoice = Invoice(
            tenant_id=tenant.id,
            doc_type_id=doc_type_id,
            status=InvoiceStatus.ready,
            source_file_key="test/key.pdf",
            source_filename="factura.pdf",
            source_mime="application/pdf",
            fecha=fecha,
        )
        db_session.add(invoice)
        await db_session.flush()

    await add_invoice(date(2026, 5, 15))
    await add_invoice(date(2026, 5, 31))
    await add_invoice(date(2026, 4, 25))
    await add_invoice(date(2026, 4, 10))
    await add_invoice(None)
    await db_session.commit()
    await set_tenant_context(db_session, str(tenant.id))

    stats = await invoice_service.get_invoice_date_stats(db_session, tenant.id)

    assert stats.current_month == 2
    assert stats.last_30_days == 2


async def test_apply_extraction_result_links_llm_call(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    invoice = await invoice_service.create_invoice_stub(
        db_session,
        tenant.id,
        source_file_key="test/key.pdf",
        source_filename="factura.pdf",
        source_mime="application/pdf",
    )
    await db_session.commit()
    await set_tenant_context(db_session, str(tenant.id))
    invoice = await invoice_service.get_invoice(db_session, tenant.id, invoice.id)

    llm_call_id = uuid4()
    factura = Factura(
        fecha=date(2026, 5, 1),
        proveedor="Proveedor Test",
        cif_nif="B12345678",  # pragma: allowlist secret  # pragma: allowlist secret
        base_imponible=Decimal("100.00"),
        iva_percent=Decimal("21.00"),
        iva_amount=Decimal("21.00"),
        total=Decimal("121.00"),
        confidence=0.9,
    )

    await invoice_service.apply_extraction_result(
        db_session,
        invoice=invoice,
        factura=factura,
        llm_call_id=llm_call_id,
    )
    await db_session.commit()
    await set_tenant_context(db_session, str(tenant.id))

    refreshed = await invoice_service.get_invoice(db_session, tenant.id, invoice.id)
    assert refreshed.llm_call_id == llm_call_id
    assert refreshed.status == InvoiceStatus.ready


async def test_apply_extraction_result_persists_vat_breakdown(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    invoice = await invoice_service.create_invoice_stub(
        db_session,
        tenant.id,
        source_file_key="test/key.pdf",
        source_filename="factura-mixta.pdf",
        source_mime="application/pdf",
    )
    await db_session.commit()
    await set_tenant_context(db_session, str(tenant.id))
    invoice = await invoice_service.get_invoice(db_session, tenant.id, invoice.id)

    factura = Factura(
        fecha=date(2024, 6, 15),
        proveedor="Bar La Plaza S.L.",
        cif_nif="B12345678",  # pragma: allowlist secret
        base_imponible=Decimal("150.00"),
        desgloses_iva=[
            DesgloseIVA(base=Decimal("100.00"), percent=Decimal("10"), amount=Decimal("10.00")),
            DesgloseIVA(base=Decimal("50.00"), percent=Decimal("21"), amount=Decimal("10.50")),
        ],
        iva_percent=Decimal("21"),
        iva_amount=Decimal("20.50"),
        total=Decimal("170.50"),
        confidence=0.9,
    )

    await invoice_service.apply_extraction_result(
        db_session,
        invoice=invoice,
        factura=factura,
        llm_call_id=uuid4(),
    )
    await db_session.commit()
    await set_tenant_context(db_session, str(tenant.id))

    refreshed = await invoice_service.get_invoice(db_session, tenant.id, invoice.id)
    assert refreshed.vat_breakdown is not None
    assert len(refreshed.vat_breakdown) == 2
    assert refreshed.iva_amount == Decimal("20.50")
    desgloses = invoice_service.get_vat_breakdown(refreshed)
    assert len(desgloses) == 2
    assert desgloses[1].percent == Decimal("21")
