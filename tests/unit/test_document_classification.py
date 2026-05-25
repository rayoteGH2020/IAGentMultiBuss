"""Tests de clasificación heurística de documentos."""

from app.models import DocTypeCode
from app.services.document_classification import classify_from_text


def test_classify_ticket_from_simplificada() -> None:
    assert classify_from_text("FACTURA SIMPLIFICADA Nº 123") == DocTypeCode.ticket


def test_classify_ticket_from_ticket_word() -> None:
    assert classify_from_text("Ticket de compra 15/05/2026") == DocTypeCode.ticket


def test_classify_ticket_from_tique() -> None:
    assert classify_from_text("Tique regalo incluido") == DocTypeCode.ticket


def test_classify_factura_from_base_imponible() -> None:
    assert classify_from_text("Base imponible: 100,00 €") == DocTypeCode.factura


def test_ticket_markers_take_priority_over_base_imponible() -> None:
    text = "Factura simplificada. Base imponible: 10 €"
    assert classify_from_text(text) == DocTypeCode.ticket


def test_classify_empty_text_returns_none() -> None:
    assert classify_from_text("") is None
    assert classify_from_text("   ") is None
