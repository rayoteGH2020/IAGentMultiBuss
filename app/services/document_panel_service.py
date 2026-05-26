"""Construcción de filas unificadas para el panel de documentos."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from app.models import DocTypeCode
from app.schemas.document_panel import (
    PANEL_DEFAULT_DIR,
    PANEL_DEFAULT_SORT,
    PanelDocumentRow,
    PanelListParams,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.models import DocType, Invoice, Ticket


def row_from_invoice(
    invoice: Invoice,
    *,
    doc_type_code: str | None = None,
    doc_type_label: str = "Factura",
) -> PanelDocumentRow:
    """Mapea una factura a la vista de panel compartida."""

    code = doc_type_code or _doc_type_code_from_invoice(invoice)

    return PanelDocumentRow(
        kind="invoice",
        id=invoice.id,
        fecha=invoice.fecha,
        proveedor=invoice.proveedor,
        cif_nif=invoice.cif_nif,
        total=invoice.total,
        created_at=invoice.created_at,
        updated_at=invoice.updated_at,
        status=invoice.status.value,
        source_filename=invoice.source_filename,
        error_message=invoice.error_message,
        doc_type_code=code,
        doc_type_label=doc_type_label,
        invoice=invoice,
        ticket=None,
    )


def row_from_ticket(
    ticket: Ticket,
    *,
    doc_type_code: str | None = None,
    doc_type_label: str = "Ticket",
) -> PanelDocumentRow:
    """Mapea un ticket a las mismas columnas que una factura en el panel."""

    code = doc_type_code or _doc_type_code_from_ticket(ticket)

    return PanelDocumentRow(
        kind="ticket",
        id=ticket.id,
        fecha=ticket.fecha,
        proveedor=ticket.comercio,
        cif_nif=ticket.numero_ticket,
        total=ticket.total,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        status=ticket.status.value,
        source_filename=ticket.source_filename,
        error_message=ticket.error_message,
        doc_type_code=code,
        doc_type_label=doc_type_label,
        invoice=None,
        ticket=ticket,
    )


def _doc_type_code_from_invoice(invoice: Invoice) -> str:
    doc_type = getattr(invoice, "doc_type", None)

    if doc_type is not None:
        return str(doc_type.code)

    return DocTypeCode.factura.value


def _doc_type_code_from_ticket(ticket: Ticket) -> str:
    doc_type = getattr(ticket, "doc_type", None)

    if doc_type is not None:
        return str(doc_type.code)

    return DocTypeCode.ticket.value


def _doc_type_labels(doc_types: Sequence[DocType]) -> dict[str, str]:
    return {dt.code: dt.name for dt in doc_types}


def merge_panel_rows(
    invoices: Sequence[Invoice],
    tickets: Sequence[Ticket],
    *,
    doc_types: Sequence[DocType] | None = None,
) -> list[PanelDocumentRow]:
    """Combina facturas y tickets sin ordenar (la ordenación va en apply_list_params)."""

    labels = _doc_type_labels(doc_types) if doc_types else {}

    factura_label = labels.get(DocTypeCode.factura.value, "Factura")

    ticket_label = labels.get(DocTypeCode.ticket.value, "Ticket")

    rows: list[PanelDocumentRow] = []

    for inv in invoices:
        code = _doc_type_code_from_invoice(inv)

        rows.append(
            row_from_invoice(
                inv,
                doc_type_code=code,
                doc_type_label=labels.get(code, factura_label),
            ),
        )

    for tkt in tickets:
        code = _doc_type_code_from_ticket(tkt)

        rows.append(
            row_from_ticket(
                tkt,
                doc_type_code=code,
                doc_type_label=labels.get(code, ticket_label),
            ),
        )

    return rows


def filter_panel_rows(
    rows: Sequence[PanelDocumentRow],
    *,
    doc_type_code: str | None,
) -> list[PanelDocumentRow]:
    if not doc_type_code:
        return list(rows)

    return [row for row in rows if row.doc_type_code == doc_type_code]


def _sort_key(row: PanelDocumentRow, field: str) -> tuple[bool, object]:
    """Clave de ordenación con valores nulos al final en ascendente."""

    if field == "fecha":
        return (row.fecha is None, row.fecha or date.min)

    if field == "doc_type_label":
        return (False, row.doc_type_label.casefold())

    if field == "proveedor":
        return (row.proveedor is None, (row.proveedor or "").casefold())

    if field == "cif_nif":
        return (row.cif_nif is None, (row.cif_nif or "").casefold())

    if field == "total":
        return (row.total is None, row.total if row.total is not None else Decimal("-1"))

    if field == "status":
        return (False, row.status)

    if field == "created_at":
        return (False, row.created_at)

    return (False, row.created_at)


def sort_panel_rows(
    rows: Sequence[PanelDocumentRow],
    *,
    sort: str = PANEL_DEFAULT_SORT,
    dir: str = PANEL_DEFAULT_DIR,
) -> list[PanelDocumentRow]:
    reverse = dir == "desc"

    return sorted(rows, key=lambda row: _sort_key(row, sort), reverse=reverse)


def apply_list_params(
    rows: Sequence[PanelDocumentRow],
    params: PanelListParams,
) -> list[PanelDocumentRow]:
    """Filtra por tipo y ordena según los parámetros del panel."""

    filtered = filter_panel_rows(rows, doc_type_code=params.doc_type_code)

    return sort_panel_rows(filtered, sort=params.sort, dir=params.dir)
