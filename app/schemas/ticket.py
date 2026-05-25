"""Schema Pydantic de ticket/recibo extraído (módulo 1)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TicketRecibo(BaseModel):
    model_config = ConfigDict(strict=False)

    fecha: date = Field(description="Fecha de emisión del ticket o recibo")
    comercio: str = Field(description="Nombre del comercio o establecimiento emisor")
    numero_ticket: str | None = Field(
        default=None,
        description="Número de ticket o factura simplificada si aparece",
    )
    forma_pago: str | None = Field(
        default=None,
        description="Forma de pago si aparece (efectivo, tarjeta, bizum, etc.)",
    )
    base_imponible: Decimal | None = Field(
        default=None,
        ge=0,
        description="Base imponible sin IVA si aparece desglosada",
    )
    iva_percent: Decimal | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Porcentaje IVA si aparece",
    )
    iva_amount: Decimal | None = Field(
        default=None,
        ge=0,
        description="Importe del IVA en euros si aparece",
    )
    total: Decimal = Field(ge=0, description="Importe total pagado")
    currency: str = Field(default="EUR", description="ISO 4217, normalmente EUR")
    confidence: float = Field(
        ge=0,
        le=1,
        description="Tu confianza global en la extracción (0=incierto, 1=seguro)",
    )

    @field_validator("fecha", mode="before")
    @classmethod
    def _coerce_fecha(cls, v: object) -> object:
        if isinstance(v, str):
            return date.fromisoformat(v)
        return v

    @field_validator("base_imponible", "iva_percent", "iva_amount", "total", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: object) -> object:
        if isinstance(v, int | float):
            return Decimal(str(v))
        return v

    @model_validator(mode="after")
    def _check_totals_coherent(self) -> TicketRecibo:
        if self.base_imponible is not None and self.iva_amount is not None:
            suma = (self.base_imponible + self.iva_amount).quantize(Decimal("0.01"))
            total_q = self.total.quantize(Decimal("0.01"))
            if abs(suma - total_q) > Decimal("0.01"):
                object.__setattr__(self, "confidence", min(self.confidence, 0.5))
        return self
