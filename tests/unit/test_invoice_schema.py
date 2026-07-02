"""Tests del schema Factura (desglose multi-IVA)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.schemas.invoice import DesgloseIVA, Factura, vat_breakdown_from_json, vat_breakdown_to_json


def test_factura_syncs_scalars_from_multi_vat_breakdown() -> None:
    factura = Factura(
        fecha=date(2024, 6, 15),
        proveedor="Bar La Plaza S.L.",
        cif_nif="B12345678",  # pragma: allowlist secret
        base_imponible=Decimal("150.00"),
        desgloses_iva=[
            DesgloseIVA(base=Decimal("100.00"), percent=Decimal("10"), amount=Decimal("10.00")),
            DesgloseIVA(base=Decimal("50.00"), percent=Decimal("21"), amount=Decimal("10.50")),
        ],
        iva_percent=Decimal("0"),
        iva_amount=Decimal("0"),
        total=Decimal("170.50"),
        confidence=0.95,
    )
    assert factura.iva_amount == Decimal("20.50")
    assert factura.iva_percent == Decimal("21")
    assert len(factura.desgloses_iva) == 2


def test_factura_builds_single_breakdown_from_legacy_scalars() -> None:
    factura = Factura(
        fecha=date(2025, 1, 15),
        proveedor="Acme S.L.",
        cif_nif="B00000000",
        base_imponible=Decimal("100.00"),
        iva_percent=Decimal("21.00"),
        iva_amount=Decimal("21.00"),
        total=Decimal("121.00"),
        confidence=0.95,
    )
    assert len(factura.desgloses_iva) == 1
    assert factura.desgloses_iva[0].percent == Decimal("21.00")
    assert factura.desgloses_iva[0].amount == Decimal("21.00")


def test_factura_penalizes_confidence_when_totals_mismatch() -> None:
    factura = Factura(
        fecha=date(2025, 1, 15),
        proveedor="Acme S.L.",
        cif_nif="B00000000",
        base_imponible=Decimal("100.00"),
        iva_percent=Decimal("21.00"),
        iva_amount=Decimal("21.00"),
        total=Decimal("200.00"),
        confidence=0.95,
    )
    assert factura.confidence <= 0.5


def test_vat_breakdown_json_roundtrip() -> None:
    rows = [
        DesgloseIVA(base=Decimal("100"), percent=Decimal("10"), amount=Decimal("10")),
        DesgloseIVA(base=Decimal("50"), percent=Decimal("21"), amount=Decimal("10.50")),
    ]
    payload = vat_breakdown_to_json(rows)
    restored = vat_breakdown_from_json(payload)
    assert len(restored) == 2
    assert restored[0].amount == Decimal("10")
    assert restored[1].percent == Decimal("21")
