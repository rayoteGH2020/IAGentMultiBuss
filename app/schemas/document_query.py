"""Proyecciones y filtros de consulta documental (módulo 1.5)."""

from __future__ import annotations

import enum
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.invoice import DesgloseIVA
from app.schemas.pagination import Page


class DocumentSearchFilters(BaseModel):
    """Filtros comunes y específicos por tipo; el handler ignora campos no aplicables."""

    fecha_from: date | None = None
    fecha_to: date | None = None
    total_min: Decimal | None = Field(default=None, ge=0)
    total_max: Decimal | None = Field(default=None, ge=0)
    status: list[str] | None = None
    text_query: str | None = Field(
        default=None,
        description="Búsqueda amplia en campos de texto del documento",
    )
    proveedor_query: str | None = Field(
        default=None,
        description="Solo facturas: nombre de proveedor (tolerante a tildes)",
    )
    cif_nif: str | None = Field(
        default=None,
        description="CIF/NIF parcial (facturas, contratos, seguros)",
    )
    comercio_query: str | None = Field(
        default=None,
        description="Solo tickets: nombre de comercio",
    )
    numero_ticket: str | None = Field(default=None, description="Solo tickets")
    forma_pago: str | None = Field(default=None, description="Solo tickets")
    parte_contraria_query: str | None = Field(
        default=None,
        description="Solo contratos: nombre de la parte contraria",
    )
    numero_contrato: str | None = Field(default=None, description="Solo contratos")
    aseguradora_query: str | None = Field(
        default=None,
        description="Solo seguros: nombre de la aseguradora",
    )
    numero_poliza: str | None = Field(default=None, description="Solo seguros")
    tipo_seguro: str | None = Field(default=None, description="Solo seguros")
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class InvoiceLineRead(BaseModel):
    """Línea de factura en detalle (sin raw_extraction)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    descripcion: str
    cantidad: Decimal
    precio_unitario: Decimal
    total: Decimal
    position: int


class InvoiceRead(BaseModel):
    """Proyección de factura para tools y chat (sin PII pesada ni R2 keys)."""

    model_config = ConfigDict(from_attributes=True)

    doc_type_code: Literal["factura"] = "factura"
    id: UUID
    status: str
    fecha: date | None = None
    proveedor: str | None = None
    cif_nif: str | None = None
    base_imponible: Decimal | None = None
    iva_percent: Decimal | None = None
    iva_amount: Decimal | None = None
    desgloses_iva: list[DesgloseIVA] = Field(default_factory=list)
    total: Decimal | None = None
    currency: str = "EUR"
    confidence: Decimal | None = None
    source_filename: str | None = None
    lineas: list[InvoiceLineRead] = Field(default_factory=list)


class TicketRead(BaseModel):
    """Proyección de ticket para tools y chat."""

    model_config = ConfigDict(from_attributes=True)

    doc_type_code: Literal["ticket"] = "ticket"
    id: UUID
    status: str
    fecha: date | None = None
    comercio: str | None = None
    numero_ticket: str | None = None
    forma_pago: str | None = None
    base_imponible: Decimal | None = None
    iva_percent: Decimal | None = None
    iva_amount: Decimal | None = None
    total: Decimal | None = None
    currency: str = "EUR"
    confidence: Decimal | None = None
    source_filename: str | None = None


class ContractRead(BaseModel):
    """Proyección de contrato para tools y chat."""

    model_config = ConfigDict(from_attributes=True)

    doc_type_code: Literal["contrato"] = "contrato"
    id: UUID
    status: str
    titulo: str | None = None
    numero_contrato: str | None = None
    parte_contraria: str | None = None
    cif_nif: str | None = None
    fecha_inicio: date | None = None
    fecha_fin: date | None = None
    importe: Decimal | None = None
    currency: str = "EUR"
    objeto: str | None = None
    confidence: Decimal | None = None
    source_filename: str | None = None


class InsuranceRead(BaseModel):
    """Proyección de póliza de seguro para tools y chat."""

    model_config = ConfigDict(from_attributes=True)

    doc_type_code: Literal["seguro"] = "seguro"
    id: UUID
    status: str
    aseguradora: str | None = None
    numero_poliza: str | None = None
    tomador: str | None = None
    cif_nif: str | None = None
    tipo_seguro: str | None = None
    fecha_inicio: date | None = None
    fecha_fin: date | None = None
    prima: Decimal | None = None
    currency: str = "EUR"
    cobertura: str | None = None
    confidence: Decimal | None = None
    source_filename: str | None = None


DocumentRead = Annotated[
    InvoiceRead | TicketRead | ContractRead | InsuranceRead,
    Field(discriminator="doc_type_code"),
]

DocumentPage = Page[DocumentRead]


class AggregateMetric(enum.StrEnum):
    """Métricas de agregación (nombres distintos de ``count`` por colisión con str)."""

    metric_count = "count"
    sum_total = "sum_total"


class AggregateGroupBy(enum.StrEnum):
    none = "none"
    proveedor = "proveedor"
    comercio = "comercio"
    parte_contraria = "parte_contraria"
    aseguradora = "aseguradora"
    month = "month"
    year = "year"
    status = "status"


class AggregateRow(BaseModel):
    """Fila de agregación cuando hay group_by."""

    group_key: str
    value: Decimal | int


class AggregateResult(BaseModel):
    """Resultado de agregación sobre documentos de un tipo."""

    doc_type_code: str
    metric: AggregateMetric
    group_by: AggregateGroupBy
    rows: list[AggregateRow] = Field(default_factory=list)
    total_value: Decimal | int | None = Field(
        default=None,
        description="Valor único cuando group_by es none",
    )
