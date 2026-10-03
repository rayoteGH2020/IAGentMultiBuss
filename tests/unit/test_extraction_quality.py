"""Tests de calidad post-extracción y display multi-IVA."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from app.schemas.contract import ContratoDocumento
from app.schemas.document_panel import PanelDocumentRow
from app.schemas.insurance import SeguroPoliza
from app.schemas.invoice import Factura
from app.schemas.ticket import TicketRecibo
from app.services.extraction_quality import (
    contract_extraction_is_usable,
    insurance_extraction_is_usable,
    invoice_extraction_is_usable,
    ticket_extraction_is_usable,
)


def test_invoice_low_confidence_unusable() -> None:
    factura = Factura(
        fecha=date(2024, 1, 1),
        proveedor="ACME",
        base_imponible=Decimal("100"),
        iva_percent=Decimal("21"),
        iva_amount=Decimal("21"),
        total=Decimal("121"),
        confidence=0.1,
    )
    assert invoice_extraction_is_usable(factura) is False


def test_invoice_empty_garbage_unusable() -> None:
    factura = Factura(
        fecha=date(2024, 1, 1),
        proveedor="n/a",
        base_imponible=Decimal("0"),
        iva_percent=Decimal("0"),
        iva_amount=Decimal("0"),
        total=Decimal("0"),
        confidence=0.9,
    )
    assert invoice_extraction_is_usable(factura) is False


def test_invoice_valid_usable() -> None:
    factura = Factura(
        fecha=date(2024, 1, 1),
        proveedor="ACME S.L.",
        base_imponible=Decimal("100"),
        iva_percent=Decimal("21"),
        iva_amount=Decimal("21"),
        total=Decimal("121"),
        confidence=0.9,
    )
    assert invoice_extraction_is_usable(factura) is True


def test_ticket_without_comercio_or_amount_unusable() -> None:
    recibo = TicketRecibo(
        fecha=date(2024, 1, 1),
        comercio="desconocido",
        total=Decimal("0"),
        confidence=0.8,
    )
    assert ticket_extraction_is_usable(recibo) is False


def test_contract_and_insurance_quality_gates() -> None:
    bad_contract = ContratoDocumento(
        titulo="n/a",
        parte_contraria="desconocido",
        fecha_inicio=date(2024, 1, 1),
        confidence=0.9,
    )
    assert contract_extraction_is_usable(bad_contract) is False

    good_contract = ContratoDocumento(
        titulo="Contrato de servicios",
        parte_contraria="Proveedor SA",
        fecha_inicio=date(2024, 1, 1),
        confidence=0.9,
    )
    assert contract_extraction_is_usable(good_contract) is True

    bad_insurance = SeguroPoliza(
        aseguradora="n/a",
        tomador="Yo",
        fecha_inicio=date(2024, 1, 1),
        confidence=0.2,
    )
    assert insurance_extraction_is_usable(bad_insurance) is False


def test_panel_iva_percent_label_multiple() -> None:
    now = datetime.now(tz=UTC)
    row = PanelDocumentRow(
        kind="invoice",
        id=uuid4(),
        fecha=date(2024, 6, 1),
        proveedor="Bar",
        cif_nif=None,
        base_imponible=Decimal("150"),
        iva_percent=Decimal("21"),
        iva_amount=Decimal("20.50"),
        total=Decimal("170.50"),
        created_at=now,
        updated_at=now,
        status="ready",
        source_filename="x.pdf",
        error_message=None,
        doc_type_code="factura",
        doc_type_label="Factura",
        vat_tranche_count=2,
    )
    assert row.iva_percent_label == "Múltiple"


def test_panel_iva_percent_label_single() -> None:
    now = datetime.now(tz=UTC)
    row = PanelDocumentRow(
        kind="invoice",
        id=uuid4(),
        fecha=date(2024, 6, 1),
        proveedor="Bar",
        cif_nif=None,
        base_imponible=Decimal("100"),
        iva_percent=Decimal("21"),
        iva_amount=Decimal("21"),
        total=Decimal("121"),
        created_at=now,
        updated_at=now,
        status="ready",
        source_filename="x.pdf",
        error_message=None,
        doc_type_code="factura",
        doc_type_label="Factura",
        vat_tranche_count=1,
    )
    assert row.iva_percent_label == "21.00 %"
