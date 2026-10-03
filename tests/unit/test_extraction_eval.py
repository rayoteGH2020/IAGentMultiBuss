"""Tests del comparador de campos de evals de extracción."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.evals.field_compare import (
    cif_match,
    compare_factura,
    ground_truth_is_usable,
    proveedor_match,
)
from app.schemas.invoice import DesgloseIVA, Factura


def test_compare_includes_vat_breakdown_when_ground_truth_has_desgloses() -> None:
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
        confidence=0.95,
    )
    gt = {
        "fecha": "2024-06-15",
        "proveedor": "Bar La Plaza",
        "cif_nif": "B12345678",  # pragma: allowlist secret
        "total": "170.50",
        "base_imponible": "150.00",
        "iva_amount": "20.50",
        "desgloses_iva": [
            {"base": "100.00", "percent": "10", "amount": "10.00"},
            {"base": "50.00", "percent": "21", "amount": "10.50"},
        ],
    }
    results = compare_factura(factura, gt)
    vat_result = next(r for r in results if r[0] == "desgloses_iva")
    assert vat_result[3] is True


def test_proveedor_match_compact_substring() -> None:
    assert proveedor_match("PC Componentes", "PC Componentes y Multimedia SLU") is True


def test_proveedor_match_token_overlap() -> None:
    assert (
        proveedor_match("Curenergía", "CURENERGÍA COMERCIALIZADOR DE ÚLTIMO RECURSO S.A.U.") is True
    )


def test_cif_match_skips_empty_ground_truth() -> None:
    assert cif_match("", None) is True
    assert cif_match("", "B12345678") is True  # pragma: allowlist secret


def test_ground_truth_is_usable_rejects_empty_placeholders() -> None:
    assert ground_truth_is_usable(None) is False
    assert ground_truth_is_usable({"fecha": "", "total": "", "proveedor": ""}) is False
    assert (
        ground_truth_is_usable({"fecha": "2024-01-01", "total": "10.00", "proveedor": "Acme"})
        is True
    )
