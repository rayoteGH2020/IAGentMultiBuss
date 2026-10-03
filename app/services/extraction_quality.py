"""Gates de calidad post-extracción: no persistir basura como documento ready."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.contract import ContratoDocumento
    from app.schemas.insurance import SeguroPoliza
    from app.schemas.invoice import Factura
    from app.schemas.ticket import TicketRecibo

MIN_EXTRACTION_CONFIDENCE = 0.30

_PLACEHOLDER_NAMES = frozenset(
    {
        "",
        "-",
        "n/a",
        "na",
        "null",
        "none",
        "desconocido",
        "unknown",
        "sin datos",
        "no identificado",
    },
)

UNUSABLE_EXTRACTION_TECHNICAL = "empty_or_unusable_extraction"


def _is_placeholder_name(value: str | None) -> bool:
    if value is None:
        return True
    return value.strip().lower() in _PLACEHOLDER_NAMES


def invoice_extraction_is_usable(factura: Factura) -> bool:
    """True si la extracción de factura tiene señal útil para marcar ready."""
    if factura.confidence < MIN_EXTRACTION_CONFIDENCE:
        return False
    has_amount = factura.total > 0 or factura.base_imponible > 0
    has_provider = not _is_placeholder_name(factura.proveedor)
    if not has_provider and not has_amount:
        return False
    return has_amount or factura.fecha is not None


def ticket_extraction_is_usable(recibo: TicketRecibo) -> bool:
    """True si la extracción de ticket tiene señal útil para marcar ready."""
    if recibo.confidence < MIN_EXTRACTION_CONFIDENCE:
        return False
    has_amount = recibo.total > 0 or (
        recibo.base_imponible is not None and recibo.base_imponible > 0
    )
    has_comercio = not _is_placeholder_name(recibo.comercio)
    if not has_comercio and not has_amount:
        return False
    return has_amount or recibo.fecha is not None


def contract_extraction_is_usable(data: ContratoDocumento) -> bool:
    """True si la extracción de contrato tiene señal útil para marcar ready."""
    if data.confidence < MIN_EXTRACTION_CONFIDENCE:
        return False
    if _is_placeholder_name(data.parte_contraria):
        return False
    return not _is_placeholder_name(data.titulo)


def insurance_extraction_is_usable(data: SeguroPoliza) -> bool:
    """True si la extracción de seguro tiene señal útil para marcar ready."""
    if data.confidence < MIN_EXTRACTION_CONFIDENCE:
        return False
    return not _is_placeholder_name(data.aseguradora)
