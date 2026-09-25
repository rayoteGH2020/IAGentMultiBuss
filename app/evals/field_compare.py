"""Comparación de campos extraídos vs ground truth (evals módulo 1)."""

from __future__ import annotations

import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, TypeGuard

if TYPE_CHECKING:
    from app.schemas.invoice import DesgloseIVA, Factura

_LEGAL_SUFFIXES = re.compile(
    r"\b(s\.?l\.?u?\.?|s\.?a\.?u?\.?|s\.?l\.?|s\.?a\.?|slu|sa|sl)\b",
    re.IGNORECASE,
)


def gt_field_empty(value: object) -> bool:
    """True si el ground truth no exige comparar este campo."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def decimal_eq(a: str, b: str, tolerance: Decimal = Decimal("0.01")) -> bool:
    try:
        return abs(Decimal(a) - Decimal(b)) <= tolerance
    except (InvalidOperation, ValueError):
        return False


def normalize_proveedor(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    ascii_name = _LEGAL_SUFFIXES.sub("", ascii_name)
    ascii_name = re.sub(r"[^\w\s]", " ", ascii_name, flags=re.UNICODE)
    return " ".join(ascii_name.lower().split())


def proveedor_match(expected: str, actual: str) -> bool:
    if gt_field_empty(expected):
        return True
    exp = normalize_proveedor(expected)
    act = normalize_proveedor(actual)
    if exp in act or act in exp:
        return True
    exp_compact = re.sub(r"\s+", "", exp)
    act_compact = re.sub(r"\s+", "", act)
    if exp_compact and exp_compact in act_compact:
        return True
    if act_compact and act_compact in exp_compact:
        return True
    exp_tokens = [t for t in exp.split() if len(t) >= 4]
    if not exp_tokens:
        exp_tokens = [t for t in exp.split() if len(t) >= 3]
    if not exp_tokens:
        return False
    matched = sum(1 for token in exp_tokens if token in act)
    return matched >= max(1, (len(exp_tokens) * 2 + 2) // 3)


def cif_match(expected: str, actual: str | None) -> bool:
    if gt_field_empty(expected):
        return True
    if actual is None:
        return False
    return expected.upper().replace("-", "").replace(" ", "") == actual.upper()


def vat_breakdown_eq(expected: list[dict[str, str]], actual: list[DesgloseIVA]) -> bool:
    if len(expected) != len(actual):
        return False

    def _sort_key_dict(d: dict[str, str]) -> tuple[str, str]:
        return (str(d.get("percent", "")), str(d.get("base", "")))

    def _sort_key_model(d: DesgloseIVA) -> tuple[str, str]:
        return (str(d.percent), str(d.base))

    exp_sorted = sorted(expected, key=_sort_key_dict)
    act_sorted = sorted(actual, key=_sort_key_model)
    for exp, act in zip(exp_sorted, act_sorted, strict=True):
        if not decimal_eq(str(exp["base"]), str(act.base)):
            return False
        if not decimal_eq(str(exp["percent"]), str(act.percent)):
            return False
        if not decimal_eq(str(exp["amount"]), str(act.amount)):
            return False
    return True


def ground_truth_is_usable(gt: object) -> TypeGuard[dict[str, Any]]:
    """False si el caso no tiene anotación útil (p. ej. placeholders vacíos)."""
    if not isinstance(gt, dict):
        return False
    critical = ("fecha", "total", "proveedor")
    return any(not gt_field_empty(gt.get(key)) for key in critical)


def compare_factura(factura: Factura, gt: dict[str, Any]) -> list[tuple[str, str, str, bool]]:
    """Devuelve tuplas (field, expected, actual, match)."""
    out: list[tuple[str, str, str, bool]] = [
        (
            "fecha",
            str(gt.get("fecha", "")),
            str(factura.fecha),
            gt_field_empty(gt.get("fecha")) or str(gt.get("fecha", "")) == str(factura.fecha),
        ),
        (
            "cif_nif",
            str(gt.get("cif_nif", "")),
            str(factura.cif_nif or ""),
            cif_match(str(gt.get("cif_nif", "")), factura.cif_nif),
        ),
        (
            "proveedor",
            str(gt.get("proveedor", "")),
            factura.proveedor,
            proveedor_match(str(gt.get("proveedor", "")), factura.proveedor),
        ),
        (
            "total",
            str(gt.get("total", "")),
            str(factura.total),
            gt_field_empty(gt.get("total"))
            or decimal_eq(str(gt.get("total", "")), str(factura.total)),
        ),
        (
            "base_imponible",
            str(gt.get("base_imponible", "")),
            str(factura.base_imponible),
            gt_field_empty(gt.get("base_imponible"))
            or decimal_eq(str(gt.get("base_imponible", "")), str(factura.base_imponible)),
        ),
        (
            "iva_amount",
            str(gt.get("iva_amount", "")),
            str(factura.iva_amount),
            gt_field_empty(gt.get("iva_amount"))
            or decimal_eq(str(gt.get("iva_amount", "")), str(factura.iva_amount)),
        ),
    ]
    desgloses_gt = gt.get("desgloses_iva")
    if isinstance(desgloses_gt, list) and desgloses_gt:
        out.append(
            (
                "desgloses_iva",
                json.dumps(desgloses_gt, ensure_ascii=False),
                json.dumps(
                    [d.model_dump(mode="json") for d in factura.desgloses_iva],
                    ensure_ascii=False,
                ),
                vat_breakdown_eq(desgloses_gt, factura.desgloses_iva),
            ),
        )
    return out
