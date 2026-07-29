"""Schema Pydantic de factura emitida/extraída (módulo 1).

Este módulo define el contrato entre tres capas:
  - El LLM (via Instructor): rellena estos schemas con structured output.
  - invoice_service.apply_extraction_result: lee los campos y los persiste en BD.
  - El eval runner (app/evals/runners/extraction.py): compara campos contra ground truth.

Los atributos `description` de cada Field NO son documentación interna: Instructor
los incluye en el schema JSON que envía al LLM como parte del prompt, por lo que
su redacción afecta directamente a la calidad de la extracción.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_CIF_NIF_PATTERN = re.compile(r"^[A-Z0-9]{2,15}$")


class DesgloseIVA(BaseModel):
    """Un tramo de IVA (base, tipo y cuota) dentro de una factura."""

    model_config = ConfigDict(strict=False)

    base: Decimal = Field(ge=0, description="Base imponible a este tipo de IVA")
    percent: Decimal = Field(
        ge=0,
        le=100,
        description="Tipo IVA aplicado en este tramo (0, 4, 10, 21, ...)",
    )
    amount: Decimal = Field(ge=0, description="Cuota de IVA de este tramo en euros")

    @field_validator("base", "percent", "amount", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: object) -> object:
        if isinstance(v, int | float):
            return Decimal(str(v))
        return v


class LineaFactura(BaseModel):
    model_config = ConfigDict(strict=False)

    descripcion: str = Field(description="Descripción del concepto o producto")
    cantidad: Decimal = Field(ge=0, description="Cantidad o unidades")
    precio_unitario: Decimal = Field(ge=0, description="Precio por unidad sin IVA")
    total: Decimal = Field(ge=0, description="Total de la línea (cantidad x precio)")

    @field_validator("cantidad", "precio_unitario", "total", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: object) -> object:
        if isinstance(v, int | float):
            return Decimal(str(v))
        return v


class Factura(BaseModel):
    model_config = ConfigDict(strict=False)

    fecha: date = Field(description="Fecha de emisión de la factura")
    proveedor: str = Field(description="Razón social o nombre del emisor")
    cif_nif: str | None = Field(
        default=None,
        description=(
            "CIF, NIF, NIE o VAT intracomunitario del emisor. Null si no aparece en el documento."
        ),
    )
    numero_factura: str | None = Field(
        default=None,
        description="Número o serie de factura si aparece",
    )

    base_imponible: Decimal = Field(ge=0, description="Suma de bases imponibles sin IVA")
    desgloses_iva: list[DesgloseIVA] = Field(
        default_factory=list,
        description=(
            "Todos los tramos de IVA detectados en la factura. "
            "Un elemento por cada tipo distinto (p. ej. 10% y 21% en hostelería)."
        ),
    )
    iva_percent: Decimal = Field(
        ge=0,
        le=100,
        description=(
            "Porcentaje IVA agregado: el tipo más alto si hay varios tramos "
            "(0, 4, 10, 21). Debe cuadrar con desgloses_iva."
        ),
    )
    iva_amount: Decimal = Field(
        ge=0,
        description="Suma total de cuotas IVA de todos los tramos",
    )
    total: Decimal = Field(ge=0, description="Total final con IVA")
    currency: str = Field(default="EUR", description="ISO 4217, normalmente EUR")

    lineas: list[LineaFactura] = Field(
        default_factory=list,
        description="Líneas de detalle si aparecen",
    )

    confidence: float = Field(
        ge=0,
        le=1,
        description="Tu confianza global en la extracción (0=incierto, 1=seguro)",
    )

    @field_validator("cif_nif", mode="before")
    @classmethod
    def _normalize_cif_nif(cls, v: object) -> object:
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        cleaned = v.strip()
        if not cleaned or cleaned.lower() == "null":
            return None
        normalized = (
            unicodedata.normalize("NFKD", cleaned)
            .encode("ascii", "ignore")
            .decode("ascii")
            .upper()
            .replace("-", "")
            .replace(" ", "")
        )
        if not _CIF_NIF_PATTERN.fullmatch(normalized):
            return None
        return normalized

    @field_validator("fecha", mode="before")
    @classmethod
    def _coerce_fecha(cls, v: object) -> object:
        if isinstance(v, str):
            return date.fromisoformat(v)
        return v

    @field_validator(
        "base_imponible",
        "iva_percent",
        "iva_amount",
        "total",
        mode="before",
    )
    @classmethod
    def _coerce_decimal(cls, v: object) -> object:
        if isinstance(v, int | float):
            return Decimal(str(v))
        return v

    @model_validator(mode="after")
    def _normalize_vat_and_check_totals(self) -> Factura:
        desgloses = list(self.desgloses_iva)
        if desgloses:
            total_iva = sum((d.amount for d in desgloses), Decimal("0"))
            object.__setattr__(self, "iva_amount", total_iva.quantize(Decimal("0.01")))
            object.__setattr__(self, "iva_percent", max(d.percent for d in desgloses))
        elif self.iva_percent is not None and self.iva_amount is not None:
            object.__setattr__(
                self,
                "desgloses_iva",
                [
                    DesgloseIVA(
                        base=self.base_imponible,
                        percent=self.iva_percent,
                        amount=self.iva_amount,
                    ),
                ],
            )

        suma = (self.base_imponible + self.iva_amount).quantize(Decimal("0.01"))
        total_q = self.total.quantize(Decimal("0.01"))
        if abs(suma - total_q) > Decimal("0.01"):
            object.__setattr__(self, "confidence", min(self.confidence, 0.5))
        return self


def vat_breakdown_to_json(desgloses: list[DesgloseIVA]) -> list[dict[str, str]]:
    """Serializa desgloses para columna JSONB."""
    return [
        {
            "base": str(d.base),
            "percent": str(d.percent),
            "amount": str(d.amount),
        }
        for d in desgloses
    ]


def vat_breakdown_from_json(items: list[dict[str, Any]] | None) -> list[DesgloseIVA]:
    """Parsea desgloses persistidos en BD."""
    if not items:
        return []
    return [DesgloseIVA.model_validate(row) for row in items]
