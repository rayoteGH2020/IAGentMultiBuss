"""Comparación de tickets, contratos y pólizas extraídos vs ground truth (evals módulo 1).

Formato del ground truth por campo:

- valor escalar (``"2026-10-01"``, ``"412.48"``, ``"IKEA"``): debe coincidir;
- ``{"any_of": [...]}``: vale cualquiera de las alternativas (``null`` incluido).
  Se usa donde el schema es ambiguo: p. ej. ``fecha_inicio`` de contrato admite
  firma o inicio, e ``importe`` admite cuota periódica o total.

Contratos: ``partes`` lista las dos partes (``nombre`` + ``cif_nif``). El modelo
puede elegir cualquiera como ``parte_contraria``, pero su ``cif_nif`` debe ser el
de la parte elegida.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.evals.field_compare import decimal_eq, proveedor_match

if TYPE_CHECKING:
    from pydantic import BaseModel

FieldComparison = tuple[str, str, str, bool]
Matcher = Callable[[str, str], bool]


def _code(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _code_eq(expected: str, actual: str) -> bool:
    return _code(expected) == _code(actual)


def _text_eq(expected: str, actual: str) -> bool:
    return expected == actual


def _name_eq(expected: str, actual: str) -> bool:
    return bool(actual) and proveedor_match(expected, actual)


def _contains_ci(expected: str, actual: str) -> bool:
    return expected.casefold() in actual.casefold()


def _payment_eq(expected: str, actual: str) -> bool:
    """Forma de pago: ``tarjeta`` acepta cualquier variante de pago con tarjeta."""
    if expected.casefold() == "tarjeta":
        return bool(re.search(r"tarjeta|visa|mastercard|card|contactless|d[eé]bito", actual, re.I))
    return _contains_ci(expected, actual)


def _as_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _alternatives(spec: object) -> list[object]:
    if isinstance(spec, dict) and "any_of" in spec:
        return list(spec["any_of"])
    return [spec]


def _matches(spec: object, actual: object, matcher: Matcher) -> bool:
    actual_str = _as_str(actual)
    for alt in _alternatives(spec):
        if alt is None:
            if actual is None or actual_str == "":
                return True
            continue
        if actual is None or actual_str == "":
            continue
        if matcher(str(alt), actual_str):
            return True
    return False


def _compare_fields(
    obj: BaseModel,
    gt: dict[str, Any],
    matchers: dict[str, Matcher],
) -> list[FieldComparison]:
    out: list[FieldComparison] = []
    for field, matcher in matchers.items():
        if field not in gt:
            continue
        actual = getattr(obj, field)
        out.append(
            (field, str(gt[field]), _as_str(actual), _matches(gt[field], actual, matcher)),
        )
    return out


_TICKET_MATCHERS: dict[str, Matcher] = {
    "fecha": _text_eq,
    "comercio": _name_eq,
    "numero_ticket": _code_eq,
    "forma_pago": _payment_eq,
    "base_imponible": decimal_eq,
    "iva_percent": decimal_eq,
    "iva_amount": decimal_eq,
    "total": decimal_eq,
}

_CONTRACT_MATCHERS: dict[str, Matcher] = {
    "numero_contrato": _code_eq,
    "fecha_inicio": _text_eq,
    "fecha_fin": _text_eq,
    "importe": decimal_eq,
}

_INSURANCE_MATCHERS: dict[str, Matcher] = {
    "aseguradora": _name_eq,
    "numero_poliza": _code_eq,
    "tomador": _name_eq,
    "cif_nif": _code_eq,
    "fecha_inicio": _text_eq,
    "fecha_fin": _text_eq,
    "prima": decimal_eq,
}


def compare_ticket(ticket: BaseModel, gt: dict[str, Any]) -> list[FieldComparison]:
    """Compara un ``TicketRecibo`` con su ground truth."""
    return _compare_fields(ticket, gt, _TICKET_MATCHERS)


def compare_insurance(poliza: BaseModel, gt: dict[str, Any]) -> list[FieldComparison]:
    """Compara una ``SeguroPoliza`` con su ground truth."""
    return _compare_fields(poliza, gt, _INSURANCE_MATCHERS)


def compare_contract(contrato: BaseModel, gt: dict[str, Any]) -> list[FieldComparison]:
    """Compara un ``ContratoDocumento`` con su ground truth (incluye coherencia de partes)."""
    out = _compare_fields(contrato, gt, _CONTRACT_MATCHERS)
    partes: list[dict[str, str]] = gt.get("partes", [])
    if not partes:
        return out
    parte_contraria = _as_str(getattr(contrato, "parte_contraria", None))
    cif_nif = _as_str(getattr(contrato, "cif_nif", None))
    chosen = next((p for p in partes if _name_eq(p["nombre"], parte_contraria)), None)
    expected_names = " | ".join(p["nombre"] for p in partes)
    out.append(("parte_contraria", expected_names, parte_contraria, chosen is not None))
    out.append(
        (
            "cif_nif",
            chosen["cif_nif"] if chosen else "(cif de la parte elegida)",
            cif_nif,
            chosen is not None and _code_eq(chosen["cif_nif"], cif_nif),
        ),
    )
    return out
