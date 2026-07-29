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

    from app.models import Contract, DocType, Insurance, Invoice, Ticket


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
        base_imponible=invoice.base_imponible,
        iva_percent=invoice.iva_percent,
        iva_amount=invoice.iva_amount,
        total=invoice.total,
        created_at=invoice.created_at,
        updated_at=invoice.updated_at,
        status=invoice.status.value,
        source_filename=invoice.source_filename,
        error_message=invoice.error_message,
        error_code=invoice.error_code,
        doc_type_code=code,
        doc_type_label=doc_type_label,
        invoice=invoice,
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
        base_imponible=ticket.base_imponible,
        iva_percent=ticket.iva_percent,
        iva_amount=ticket.iva_amount,
        total=ticket.total,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        status=ticket.status.value,
        source_filename=ticket.source_filename,
        error_message=ticket.error_message,
        error_code=ticket.error_code,
        doc_type_code=code,
        doc_type_label=doc_type_label,
        ticket=ticket,
    )


def row_from_contract(
    contract: Contract,
    *,
    doc_type_code: str | None = None,
    doc_type_label: str = "Contrato",
) -> PanelDocumentRow:
    """Mapea un contrato a las columnas del panel (proveedor=parte, total=importe)."""
    code = doc_type_code or _doc_type_code_from_contract(contract)
    return PanelDocumentRow(
        kind="contract",
        id=contract.id,
        fecha=contract.fecha_inicio,
        proveedor=contract.parte_contraria,
        cif_nif=contract.cif_nif,
        base_imponible=None,
        iva_percent=None,
        iva_amount=None,
        total=contract.importe,
        created_at=contract.created_at,
        updated_at=contract.updated_at,
        status=contract.status.value,
        source_filename=contract.source_filename,
        error_message=contract.error_message,
        error_code=contract.error_code,
        doc_type_code=code,
        doc_type_label=doc_type_label,
        contract=contract,
    )


def row_from_insurance(
    insurance: Insurance,
    *,
    doc_type_code: str | None = None,
    doc_type_label: str = "Seguro",
) -> PanelDocumentRow:
    """Mapea una póliza a las columnas del panel (proveedor=aseguradora, total=prima)."""
    code = doc_type_code or _doc_type_code_from_insurance(insurance)
    return PanelDocumentRow(
        kind="insurance",
        id=insurance.id,
        fecha=insurance.fecha_inicio,
        proveedor=insurance.aseguradora,
        cif_nif=insurance.numero_poliza or insurance.cif_nif,
        base_imponible=None,
        iva_percent=None,
        iva_amount=None,
        total=insurance.prima,
        created_at=insurance.created_at,
        updated_at=insurance.updated_at,
        status=insurance.status.value,
        source_filename=insurance.source_filename,
        error_message=insurance.error_message,
        error_code=insurance.error_code,
        doc_type_code=code,
        doc_type_label=doc_type_label,
        insurance=insurance,
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


def _doc_type_code_from_contract(contract: Contract) -> str:
    doc_type = getattr(contract, "doc_type", None)
    if doc_type is not None:
        return str(doc_type.code)
    return DocTypeCode.contrato.value


def _doc_type_code_from_insurance(insurance: Insurance) -> str:
    doc_type = getattr(insurance, "doc_type", None)
    if doc_type is not None:
        return str(doc_type.code)
    return DocTypeCode.seguro.value


def _doc_type_labels(doc_types: Sequence[DocType]) -> dict[str, str]:
    return {dt.code: dt.name for dt in doc_types}


def merge_panel_rows(
    invoices: Sequence[Invoice],
    tickets: Sequence[Ticket],
    *,
    contracts: Sequence[Contract] | None = None,
    insurances: Sequence[Insurance] | None = None,
    doc_types: Sequence[DocType] | None = None,
) -> list[PanelDocumentRow]:
    """Combina documentos del módulo 1 sin ordenar (ordenación en apply_list_params)."""
    labels = _doc_type_labels(doc_types) if doc_types else {}
    factura_label = labels.get(DocTypeCode.factura.value, "Factura")
    ticket_label = labels.get(DocTypeCode.ticket.value, "Ticket")
    contrato_label = labels.get(DocTypeCode.contrato.value, "Contrato")
    seguro_label = labels.get(DocTypeCode.seguro.value, "Seguro")

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
    for ctr in contracts or ():
        code = _doc_type_code_from_contract(ctr)
        rows.append(
            row_from_contract(
                ctr,
                doc_type_code=code,
                doc_type_label=labels.get(code, contrato_label),
            ),
        )
    for ins in insurances or ():
        code = _doc_type_code_from_insurance(ins)
        rows.append(
            row_from_insurance(
                ins,
                doc_type_code=code,
                doc_type_label=labels.get(code, seguro_label),
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
    if field == "base_imponible":
        return (
            row.base_imponible is None,
            row.base_imponible if row.base_imponible is not None else Decimal("-1"),
        )
    if field == "iva_percent":
        return (
            row.iva_percent is None,
            row.iva_percent if row.iva_percent is not None else Decimal("-1"),
        )
    if field == "iva_amount":
        return (
            row.iva_amount is None,
            row.iva_amount if row.iva_amount is not None else Decimal("-1"),
        )
    if field == "total":
        return (row.total is None, row.total if row.total is not None else Decimal("-1"))
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


def partition_just_uploaded(
    rows: Sequence[PanelDocumentRow],
    just_uploaded_ids: Sequence[str],
) -> tuple[list[PanelDocumentRow], list[PanelDocumentRow]]:
    """Separa filas recién subidas (orden de subida) del resto del listado.

    Tras una subida, el panel muestra primero los ficheros encolados y debajo
    el histórico ya ordenado (p. ej. created_at desc).
    """
    if not just_uploaded_ids:
        return [], list(rows)

    id_set = set(just_uploaded_ids)
    by_id = {str(row.id): row for row in rows}
    just_uploaded = [by_id[doc_id] for doc_id in just_uploaded_ids if doc_id in by_id]
    others = [row for row in rows if str(row.id) not in id_set]
    return just_uploaded, others
