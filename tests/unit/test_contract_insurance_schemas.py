"""Tests de schemas de extracción contrato/seguro."""

from datetime import date
from decimal import Decimal

from app.schemas.contract import ContratoDocumento
from app.schemas.insurance import SeguroPoliza


def test_contrato_documento_coerces_types() -> None:
    doc = ContratoDocumento.model_validate(
        {
            "titulo": "Mantenimiento",
            "parte_contraria": "ACME",
            "fecha_firma": "2025-01-10",
            "fecha_inicio": "2025-01-15",
            "importe_periodico": 99.5,
            "periodicidad": "mensual",
            "importe_total": 1194,
            "confidence": 0.9,
        },
    )
    assert doc.fecha_firma == date(2025, 1, 10)
    assert doc.fecha_inicio == date(2025, 1, 15)
    assert doc.importe_periodico == Decimal("99.5")
    assert doc.importe_total == Decimal("1194")


def test_seguro_poliza_coerces_types() -> None:
    doc = SeguroPoliza.model_validate(
        {
            "aseguradora": "AXA",
            "tomador": "Pepe SL",
            "fecha_inicio": "2025-02-01",
            "prima": 120,
            "confidence": 0.8,
        },
    )
    assert doc.fecha_inicio == date(2025, 2, 1)
    assert doc.prima == Decimal("120")
