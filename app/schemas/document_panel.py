"""Vista unificada de documentos en el panel de /documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from app.core.document_processing_errors import is_retryable

if TYPE_CHECKING:
    from app.models import Invoice, LLMCall, Ticket

PANEL_SORT_COLUMNS: frozenset[str] = frozenset(
    {
        "doc_type_label",
        "fecha",
        "proveedor",
        "base_imponible",
        "iva_percent",
        "iva_amount",
        "total",
        "created_at",
    },
)
PANEL_DEFAULT_SORT = "created_at"
PANEL_DEFAULT_DIR = "desc"


@dataclass(frozen=True, slots=True)
class PanelListParams:
    """Parámetros de filtro y ordenación del panel de documentos."""

    doc_type_code: str | None = None
    sort: str = PANEL_DEFAULT_SORT
    dir: str = PANEL_DEFAULT_DIR

    @classmethod
    def from_query(
        cls,
        *,
        doc_type_code: str | None = None,
        sort: str | None = None,
        dir: str | None = None,
    ) -> PanelListParams:
        filter_code = doc_type_code.strip() if doc_type_code and doc_type_code.strip() else None
        sort_key = sort if sort in PANEL_SORT_COLUMNS else PANEL_DEFAULT_SORT
        direction = dir if dir in {"asc", "desc"} else PANEL_DEFAULT_DIR
        return cls(doc_type_code=filter_code, sort=sort_key, dir=direction)

    def next_dir_for(self, field: str) -> str:
        if self.sort == field:
            return "asc" if self.dir == "desc" else "desc"
        return "asc"


@dataclass(frozen=True, slots=True)
class PanelDocumentRow:
    """Fila normalizada para la tabla de documentos (mismas columnas que factura)."""

    kind: Literal["invoice", "ticket"]
    id: UUID
    fecha: date | None
    proveedor: str | None
    cif_nif: str | None
    base_imponible: Decimal | None
    iva_percent: Decimal | None
    iva_amount: Decimal | None
    total: Decimal | None
    created_at: datetime
    updated_at: datetime
    status: str
    source_filename: str | None
    error_message: str | None
    doc_type_code: str
    doc_type_label: str
    error_code: str | None = None
    invoice: Invoice | None = None
    ticket: Ticket | None = None

    @property
    def can_retry(self) -> bool:
        """False en rechazos por límites: el mismo fichero volvería a fallar."""
        return is_retryable(self.error_code)

    @property
    def llm_call(self) -> LLMCall | None:
        if self.invoice is not None:
            return self.invoice.llm_call
        if self.ticket is not None:
            return self.ticket.llm_call
        return None

    @property
    def status_poll_url(self) -> str:
        if self.kind == "invoice":
            return f"/jobs/invoice/{self.id}/status"
        return f"/jobs/ticket/{self.id}/status"

    @property
    def dialog_title(self) -> str:
        return f"Extracción de {self.doc_type_label.lower()}"

    @property
    def has_expandable_detail(self) -> bool:
        return (self.kind == "invoice" and self.invoice is not None) or (
            self.kind == "ticket" and self.ticket is not None
        )
