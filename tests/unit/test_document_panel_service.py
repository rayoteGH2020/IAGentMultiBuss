"""Tests del servicio de filas unificadas del panel de documentos."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from app.models import DocType, DocTypeCode, Invoice, InvoiceStatus, Ticket, TicketStatus
from app.schemas.document_panel import PanelListParams
from app.services import document_panel_service


def _invoice() -> Invoice:
    now = datetime.now(tz=UTC)

    inv = Invoice(
        id=uuid4(),
        tenant_id=uuid4(),
        doc_type_id=uuid4(),
        status=InvoiceStatus.ready,
        proveedor="Proveedor A",
        cif_nif="B11111111",
        total=Decimal("100.00"),
        fecha=date(2025, 3, 1),
        created_at=now,
        updated_at=now,
    )

    return inv


def _ticket() -> Ticket:
    older = datetime(2025, 1, 1, tzinfo=UTC)

    return Ticket(
        id=uuid4(),
        tenant_id=uuid4(),
        doc_type_id=uuid4(),
        status=TicketStatus.ready,
        comercio="Comercio B",
        numero_ticket="T-99",
        total=Decimal("12.50"),
        fecha=date(2025, 2, 1),
        created_at=older,
        updated_at=older,
    )


def test_row_from_ticket_maps_comercio_to_proveedor() -> None:
    ticket = _ticket()

    row = document_panel_service.row_from_ticket(ticket)

    assert row.kind == "ticket"

    assert row.proveedor == "Comercio B"

    assert row.cif_nif == "T-99"

    assert row.doc_type_code == DocTypeCode.ticket.value

    assert row.status_poll_url.endswith(f"/jobs/ticket/{ticket.id}/status")


def test_apply_list_params_sorts_newest_first_by_default() -> None:
    inv = _invoice()

    tkt = _ticket()

    doc_types = [
        DocType(code=DocTypeCode.factura.value, name="Factura", is_active=True),
        DocType(code=DocTypeCode.ticket.value, name="Ticket de compra", is_active=True),
    ]

    merged = document_panel_service.merge_panel_rows([inv], [tkt], doc_types=doc_types)

    rows = document_panel_service.apply_list_params(merged, PanelListParams())

    assert len(rows) == 2

    assert rows[0].kind == "invoice"

    assert rows[1].kind == "ticket"

    assert rows[0].doc_type_label == "Factura"

    assert rows[1].doc_type_label == "Ticket de compra"


def test_filter_panel_rows_by_doc_type() -> None:
    inv = _invoice()

    tkt = _ticket()

    merged = document_panel_service.merge_panel_rows([inv], [tkt])

    params = PanelListParams(doc_type_code=DocTypeCode.ticket.value)

    rows = document_panel_service.apply_list_params(merged, params)

    assert len(rows) == 1

    assert rows[0].kind == "ticket"


def test_sort_panel_rows_by_doc_type_label_asc() -> None:
    inv = _invoice()
    tkt = _ticket()
    merged = document_panel_service.merge_panel_rows([inv], [tkt])
    params = PanelListParams(sort="doc_type_label", dir="asc")
    rows = document_panel_service.apply_list_params(merged, params)
    assert rows[0].doc_type_label <= rows[1].doc_type_label


def test_sort_panel_rows_by_proveedor_asc() -> None:
    inv = _invoice()

    inv.proveedor = "Zeta"

    tkt = _ticket()

    tkt.comercio = "Alpha"

    merged = document_panel_service.merge_panel_rows([inv], [tkt])

    params = PanelListParams(sort="proveedor", dir="asc")

    rows = document_panel_service.apply_list_params(merged, params)

    assert rows[0].proveedor == "Alpha"

    assert rows[1].proveedor == "Zeta"


def test_panel_list_params_from_query_normalizes_invalid() -> None:
    params = PanelListParams.from_query(
        doc_type_code="  factura  ",
        sort="invalid",
        dir="sideways",
    )

    assert params.doc_type_code == "factura"

    assert params.sort == "created_at"

    assert params.dir == "desc"


def test_panel_list_params_toggle_direction() -> None:
    params = PanelListParams(sort="fecha", dir="desc")

    assert params.next_dir_for("fecha") == "asc"

    assert params.next_dir_for("total") == "asc"
