"""Integración: búsqueda y agregación documental por tenant."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.models import DocTypeCode, Tenant, TicketStatus
from app.schemas.contract import ContratoDocumento
from app.schemas.document_query import (
    AggregateGroupBy,
    AggregateMetric,
    DocumentSearchFilters,
)
from app.schemas.insurance import SeguroPoliza
from app.schemas.invoice import Factura, LineaFactura
from app.schemas.ticket import TicketRecibo
from app.services import (
    contract_service,
    document_query_service,
    insurance_service,
    invoice_service,
    ticket_service,
)
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _seed_invoice(
    db: AsyncSession,
    tenant_id,
    *,
    proveedor: str,
    total: Decimal,
    fecha: date,
) -> None:
    inv = await invoice_service.create_invoice_stub(
        db,
        tenant_id,
        source_file_key="test/inv.pdf",
        source_filename="inv.pdf",
        source_mime="application/pdf",
    )
    inv = await invoice_service.get_invoice(db, tenant_id, inv.id)
    factura = Factura(
        fecha=fecha,
        proveedor=proveedor,
        cif_nif="B12345678",  # pragma: allowlist secret
        base_imponible=total,
        iva_percent=Decimal("21"),
        iva_amount=Decimal("0"),
        total=total,
        lineas=[
            LineaFactura(descripcion="x", cantidad=Decimal("1"), precio_unitario=total, total=total)
        ],
        confidence=0.9,
    )
    await invoice_service.apply_extraction_result(
        db,
        invoice=inv,
        factura=factura,
        llm_call_id=uuid4(),
    )


async def _seed_ticket(
    db: AsyncSession,
    tenant_id,
    *,
    comercio: str,
    total: Decimal,
    fecha: date,
) -> None:
    ticket = await ticket_service.create_ticket_stub(
        db,
        tenant_id,
        source_file_key="test/t.jpg",
        source_filename="t.jpg",
        source_mime="image/jpeg",
    )
    recibo = TicketRecibo(
        fecha=fecha,
        comercio=comercio,
        total=total,
        confidence=0.85,
    )
    await ticket_service.apply_extraction_result(
        db,
        ticket=ticket,
        recibo=recibo,
        llm_call_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_search_documents_factura_by_proveedor(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory,
) -> None:
    # Premium: sin límite de histórico (fechas fijas del pasado, D017).
    tenant: Tenant = await tenant_factory(plan_code="premium")
    await set_tenant_context(db_session, str(tenant.id))
    await _seed_invoice(
        db_session,
        tenant.id,
        proveedor="Telefónica SA",
        total=Decimal("100.00"),
        fecha=date(2025, 4, 10),
    )
    await _seed_invoice(
        db_session,
        tenant.id,
        proveedor="Otro Proveedor",
        total=Decimal("50.00"),
        fecha=date(2025, 4, 11),
    )
    await db_session.commit()
    await set_tenant_context(db_session, str(tenant.id))

    page = await document_query_service.search_documents(
        db_session,
        tenant.id,
        doc_type_code=DocTypeCode.factura.value,
        filters=DocumentSearchFilters(proveedor_query="telefonica", limit=10),
    )
    assert page.total == 1
    assert len(page.items) == 1
    assert page.items[0].doc_type_code == "factura"
    assert page.items[0].proveedor == "Telefónica SA"


@pytest.mark.asyncio
async def test_aggregate_documents_ticket_count(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory,
) -> None:
    # Premium: sin límite de histórico (fechas fijas del pasado, D017).
    tenant: Tenant = await tenant_factory(plan_code="premium")
    await set_tenant_context(db_session, str(tenant.id))
    await _seed_ticket(
        db_session,
        tenant.id,
        comercio="Super Test",
        total=Decimal("12.00"),
        fecha=date(2025, 5, 1),
    )
    await _seed_ticket(
        db_session,
        tenant.id,
        comercio="Otro",
        total=Decimal("8.00"),
        fecha=date(2025, 5, 2),
    )
    await db_session.commit()
    await set_tenant_context(db_session, str(tenant.id))

    result = await document_query_service.aggregate_documents(
        db_session,
        tenant.id,
        doc_type_code=DocTypeCode.ticket.value,
        filters=DocumentSearchFilters(status=[TicketStatus.ready.value]),
        metric=AggregateMetric.metric_count,
        group_by=AggregateGroupBy.none,
    )
    assert result.total_value == 2


@pytest.mark.asyncio
async def test_resolve_inactive_doc_type_raises(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory,
) -> None:
    from app.core.errors import ValidationError
    from app.services import doc_type_service

    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    with pytest.raises(ValidationError, match="inactive"):
        await doc_type_service.resolve_active_doc_type(db_session, "no_existe_xyz")


async def _seed_contract(
    db: AsyncSession,
    tenant_id,
    *,
    parte_contraria: str,
    fecha_inicio: date,
    fecha_fin: date | None,
    importe_periodico: Decimal | None = Decimal("100.00"),
    periodicidad: str | None = "mensual",
    importe_total: Decimal | None = None,
) -> None:
    contract = await contract_service.create_contract_stub(
        db,
        tenant_id,
        source_file_key="test/c.pdf",
        source_filename="c.pdf",
        source_mime="application/pdf",
    )
    data = ContratoDocumento(
        titulo="Contrato de servicios",
        parte_contraria=parte_contraria,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        importe_periodico=importe_periodico,
        periodicidad=periodicidad,
        importe_total=importe_total,
        confidence=0.9,
    )
    await contract_service.apply_extraction_result(
        db,
        contract=contract,
        data=data,
        llm_call_id=uuid4(),
    )


async def _seed_insurance(
    db: AsyncSession,
    tenant_id,
    *,
    aseguradora: str,
    fecha_inicio: date,
    fecha_fin: date | None,
) -> None:
    insurance = await insurance_service.create_insurance_stub(
        db,
        tenant_id,
        source_file_key="test/s.pdf",
        source_filename="s.pdf",
        source_mime="application/pdf",
    )
    data = SeguroPoliza(
        aseguradora=aseguradora,
        tomador="Empresa Test SL",
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        prima=Decimal("300.00"),
        confidence=0.9,
    )
    await insurance_service.apply_extraction_result(
        db,
        insurance=insurance,
        data=data,
        llm_call_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_aggregate_insurances_by_expiry_date(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory,
) -> None:
    """Regresión chat_documents_v2 doc_031: el vencimiento se filtra por fecha_fin, no por inicio."""
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    await _seed_insurance(
        db_session,
        tenant.id,
        aseguradora="Mapfre",
        fecha_inicio=date(2026, 10, 1),
        fecha_fin=date(2027, 10, 1),
    )
    await _seed_insurance(
        db_session,
        tenant.id,
        aseguradora="Allianz",
        fecha_inicio=date(2026, 10, 31),
        fecha_fin=date(2027, 10, 31),
    )
    await _seed_insurance(
        db_session,
        tenant.id,
        aseguradora="AXA",
        fecha_inicio=date(2027, 10, 15),
        fecha_fin=date(2028, 10, 15),
    )
    await _seed_insurance(
        db_session,
        tenant.id,
        aseguradora="Sin vencimiento",
        fecha_inicio=date(2027, 10, 20),
        fecha_fin=None,
    )
    await db_session.commit()
    await set_tenant_context(db_session, str(tenant.id))

    expiring = DocumentSearchFilters(
        fecha_fin_from=date(2027, 10, 1),
        fecha_fin_to=date(2027, 10, 31),
    )
    result = await document_query_service.aggregate_documents(
        db_session,
        tenant.id,
        doc_type_code=DocTypeCode.seguro.value,
        filters=expiring,
        metric=AggregateMetric.metric_count,
        group_by=AggregateGroupBy.none,
    )
    assert result.total_value == 2

    page = await document_query_service.search_documents(
        db_session,
        tenant.id,
        doc_type_code=DocTypeCode.seguro.value,
        filters=expiring,
    )
    assert page.total == 2
    assert {item.aseguradora for item in page.items} == {"Mapfre", "Allianz"}

    starting = await document_query_service.aggregate_documents(
        db_session,
        tenant.id,
        doc_type_code=DocTypeCode.seguro.value,
        filters=DocumentSearchFilters(
            fecha_from=date(2027, 10, 1),
            fecha_to=date(2027, 10, 31),
        ),
        metric=AggregateMetric.metric_count,
        group_by=AggregateGroupBy.none,
    )
    assert starting.total_value == 2


@pytest.mark.asyncio
async def test_search_contracts_by_expiry_date(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    await _seed_contract(
        db_session,
        tenant.id,
        parte_contraria="Ascensores Norte",
        fecha_inicio=date(2025, 1, 1),
        fecha_fin=date(2026, 12, 31),
    )
    await _seed_contract(
        db_session,
        tenant.id,
        parte_contraria="Limpiezas Sur",
        fecha_inicio=date(2026, 12, 1),
        fecha_fin=date(2027, 11, 30),
    )
    await _seed_contract(
        db_session,
        tenant.id,
        parte_contraria="Indefinido SA",
        fecha_inicio=date(2026, 12, 15),
        fecha_fin=None,
    )
    await db_session.commit()
    await set_tenant_context(db_session, str(tenant.id))

    page = await document_query_service.search_documents(
        db_session,
        tenant.id,
        doc_type_code=DocTypeCode.contrato.value,
        filters=DocumentSearchFilters(
            fecha_fin_from=date(2026, 12, 1),
            fecha_fin_to=date(2026, 12, 31),
        ),
    )
    assert page.total == 1
    assert page.items[0].parte_contraria == "Ascensores Norte"

    open_ended_from = await document_query_service.search_documents(
        db_session,
        tenant.id,
        doc_type_code=DocTypeCode.contrato.value,
        filters=DocumentSearchFilters(fecha_fin_from=date(2027, 1, 1)),
    )
    assert [item.parte_contraria for item in open_ended_from.items] == ["Limpiezas Sur"]


@pytest.mark.asyncio
async def test_aggregate_contracts_grouped_by_expiry_month_and_year(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory,
) -> None:
    """P2b-12: "¿qué vence cada mes?" agrupa por fecha_fin, no por inicio."""
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    for parte, inicio, fin in [
        ("Ascensores Norte", date(2025, 1, 1), date(2027, 3, 31)),
        ("Limpiezas Sur", date(2026, 1, 1), date(2027, 3, 15)),
        ("Alarmas Este", date(2026, 3, 1), date(2028, 6, 30)),
        ("Indefinido SA", date(2026, 3, 10), None),
    ]:
        await _seed_contract(
            db_session, tenant.id, parte_contraria=parte, fecha_inicio=inicio, fecha_fin=fin
        )
    await db_session.commit()
    await set_tenant_context(db_session, str(tenant.id))

    async def _grouped(group_by: AggregateGroupBy) -> dict[str, object]:
        result = await document_query_service.aggregate_documents(
            db_session,
            tenant.id,
            doc_type_code=DocTypeCode.contrato.value,
            filters=DocumentSearchFilters(),
            metric=AggregateMetric.metric_count,
            group_by=group_by,
        )
        return {row.group_key: row.value for row in result.rows}

    assert await _grouped(AggregateGroupBy.expiry_month) == {
        "2027-03": 2,
        "2028-06": 1,
        "(sin vencimiento)": 1,
    }
    assert await _grouped(AggregateGroupBy.expiry_year) == {
        "2027": 2,
        "2028": 1,
        "(sin vencimiento)": 1,
    }
    # month sigue agrupando por inicio.
    assert await _grouped(AggregateGroupBy.month) == {"2025-01": 1, "2026-01": 1, "2026-03": 2}


@pytest.mark.asyncio
async def test_aggregate_insurances_grouped_by_expiry_month(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    for aseguradora, inicio, fin in [
        ("Mapfre", date(2026, 10, 1), date(2027, 10, 1)),
        ("Allianz", date(2026, 10, 31), date(2027, 10, 31)),
        ("AXA", date(2027, 10, 15), date(2028, 10, 15)),
    ]:
        await _seed_insurance(
            db_session, tenant.id, aseguradora=aseguradora, fecha_inicio=inicio, fecha_fin=fin
        )
    await db_session.commit()
    await set_tenant_context(db_session, str(tenant.id))

    result = await document_query_service.aggregate_documents(
        db_session,
        tenant.id,
        doc_type_code=DocTypeCode.seguro.value,
        filters=DocumentSearchFilters(),
        metric=AggregateMetric.metric_count,
        group_by=AggregateGroupBy.expiry_month,
    )

    assert {row.group_key: row.value for row in result.rows} == {"2027-10": 2, "2028-10": 1}


@pytest.mark.asyncio
async def test_expiry_grouping_is_rejected_for_invoices(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory,
) -> None:
    from app.core.errors import ValidationError

    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    with pytest.raises(ValidationError):
        await document_query_service.aggregate_documents(
            db_session,
            tenant.id,
            doc_type_code=DocTypeCode.factura.value,
            filters=DocumentSearchFilters(),
            metric=AggregateMetric.metric_count,
            group_by=AggregateGroupBy.expiry_month,
        )


@pytest.mark.asyncio
async def test_contract_amounts_are_stored_and_summed_as_annual_cost(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory,
) -> None:
    """P2b-3: "¿cuánto pago en contratos?" suma el coste anual, no cuotas mezcladas."""
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    await _seed_contract(
        db_session,
        tenant.id,
        parte_contraria="Garaje SL",
        fecha_inicio=date(2026, 10, 1),
        fecha_fin=None,
        importe_periodico=Decimal("95"),
        periodicidad="mensual",
    )
    await _seed_contract(
        db_session,
        tenant.id,
        parte_contraria="Ascensores SA",
        fecha_inicio=date(2026, 10, 1),
        fecha_fin=None,
        importe_periodico=Decimal("711"),
        periodicidad="trimestral",
    )
    await _seed_contract(
        db_session,
        tenant.id,
        parte_contraria="Vendedor coche",
        fecha_inicio=date(2026, 9, 18),
        fecha_fin=None,
        importe_periodico=None,
        periodicidad="unico",
        importe_total=Decimal("9800"),
    )
    await db_session.commit()
    await set_tenant_context(db_session, str(tenant.id))

    page = await document_query_service.search_documents(
        db_session,
        tenant.id,
        doc_type_code=DocTypeCode.contrato.value,
        filters=DocumentSearchFilters(parte_contraria_query="garaje"),
    )
    garaje = page.items[0]
    assert (garaje.importe_periodico, garaje.periodicidad, garaje.importe_anual) == (
        Decimal("95.00"),
        "mensual",
        Decimal("1140.00"),
    )

    total = await document_query_service.aggregate_documents(
        db_session,
        tenant.id,
        doc_type_code=DocTypeCode.contrato.value,
        filters=DocumentSearchFilters(),
        metric=AggregateMetric.sum_total,
        group_by=AggregateGroupBy.none,
    )
    assert total.total_value == Decimal("3984.00")  # 1140 + 2844; el pago único no suma

    expensive = await document_query_service.search_documents(
        db_session,
        tenant.id,
        doc_type_code=DocTypeCode.contrato.value,
        filters=DocumentSearchFilters(total_min=Decimal("2000")),
    )
    assert {item.parte_contraria for item in expensive.items} == {
        "Ascensores SA",
        "Vendedor coche",
    }
