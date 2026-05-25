"""Tests del schema TicketRecibo."""

from datetime import date
from decimal import Decimal

from app.schemas.ticket import TicketRecibo


def test_ticket_recibo_minimal() -> None:
    recibo = TicketRecibo(
        fecha=date(2026, 5, 1),
        comercio="Cafetería Central",
        total=Decimal("12.50"),
        confidence=0.9,
    )
    assert recibo.currency == "EUR"
    assert recibo.base_imponible is None


def test_ticket_recibo_penalizes_incoherent_totals() -> None:
    recibo = TicketRecibo(
        fecha=date(2026, 5, 1),
        comercio="Tienda",
        base_imponible=Decimal("10.00"),
        iva_amount=Decimal("2.00"),
        total=Decimal("20.00"),
        confidence=0.95,
    )
    assert recibo.confidence <= 0.5
