"""Schema Pydantic de factura emitida/extraída (módulo 1)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator


class LineaFactura(BaseModel):
    descripcion: str = Field(description="Descripción del concepto o producto")
    cantidad: Decimal = Field(gt=0, description="Cantidad o unidades")
    precio_unitario: Decimal = Field(ge=0, description="Precio por unidad sin IVA")
    total: Decimal = Field(ge=0, description="Total de la línea (cantidad x precio)")


class Factura(BaseModel):
    fecha: date = Field(description="Fecha de emisión de la factura")
    proveedor: str = Field(description="Razón social o nombre del emisor")
    cif_nif: str = Field(
        description="CIF, NIF o NIE del emisor en España",
        pattern=r"^[A-Z0-9]{8,10}$",
    )
    numero_factura: str | None = Field(
        default=None,
        description="Número o serie de factura si aparece",
    )

    base_imponible: Decimal = Field(ge=0, description="Suma sin IVA")
    iva_percent: Decimal = Field(
        ge=0,
        le=100,
        description="Porcentaje IVA aplicado (0, 4, 10, 21)",
    )
    iva_amount: Decimal = Field(ge=0, description="Importe del IVA en euros")
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

    @model_validator(mode="after")
    def _check_totals_coherent(self) -> Factura:
        suma = (self.base_imponible + self.iva_amount).quantize(Decimal("0.01"))
        total_q = self.total.quantize(Decimal("0.01"))
        if abs(suma - total_q) > Decimal("0.01"):
            object.__setattr__(self, "confidence", min(self.confidence, 0.5))
        return self
