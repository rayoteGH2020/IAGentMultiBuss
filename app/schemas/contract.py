"""Schema Pydantic de contrato extraído (módulo 1, ``contract_extraction_v2``)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Periodicidad = Literal["mensual", "trimestral", "semestral", "anual", "unico"]

# Pagos por año de cada periodicidad; "unico" no tiene equivalente anual.
PAYMENTS_PER_YEAR: Final[dict[str, int]] = {
    "mensual": 12,
    "trimestral": 4,
    "semestral": 2,
    "anual": 1,
}


# Sufijo para mostrar la cuota en la UI ("95,00 € / mes").
PERIOD_SUFFIX: Final[dict[str, str]] = {
    "mensual": "/ mes",
    "trimestral": "/ trimestre",
    "semestral": "/ semestre",
    "anual": "/ año",
}


def annual_amount(importe_periodico: Decimal | None, periodicidad: str | None) -> Decimal | None:
    """Importe anual equivalente de una cuota periódica (None si no se puede calcular).

    Es la cifra que se suma en "¿cuánto pago en contratos?": sumar cuotas de
    distinta periodicidad o mezclarlas con totales no significa nada.
    """
    if importe_periodico is None or periodicidad is None:
        return None
    payments = PAYMENTS_PER_YEAR.get(periodicidad)
    if payments is None:
        return None
    return (importe_periodico * payments).quantize(Decimal("0.01"))


class ContratoDocumento(BaseModel):
    model_config = ConfigDict(strict=False)

    titulo: str = Field(description="Título o denominación del contrato")
    numero_contrato: str | None = Field(
        default=None,
        description="Número o referencia del contrato si aparece",
    )
    parte_contraria: str = Field(
        description="Nombre de la otra parte (proveedor, cliente o contratista)",
    )
    cif_nif: str | None = Field(
        default=None,
        description="CIF/NIF de la parte contraria si aparece",
    )
    fecha_firma: date | None = Field(
        default=None,
        description="Fecha en que se firma el contrato (la del encabezado o el pie), si aparece",
    )
    fecha_inicio: date = Field(
        description=(
            "Fecha de inicio de la vigencia (desde cuándo produce efectos). "
            "Si el contrato no indica una fecha de inicio distinta, la de firma"
        ),
    )
    fecha_fin: date | None = Field(
        default=None,
        description="Fecha de fin o vencimiento del periodo inicial si aparece",
    )
    importe_periodico: Decimal | None = Field(
        default=None,
        ge=0,
        description=(
            "Cuota que se paga de forma repetida (renta, honorarios, cuota de mantenimiento), "
            "SIN IVA. Por la periodicidad con la que se factura o paga"
        ),
    )
    periodicidad: Periodicidad | None = Field(
        default=None,
        description=(
            "Cada cuánto se paga importe_periodico: mensual, trimestral, semestral o anual. "
            "'unico' si hay un solo pago (compraventa). Null si el contrato no tiene precio"
        ),
    )
    importe_total: Decimal | None = Field(
        default=None,
        ge=0,
        description=(
            "Valor económico total del contrato SIN IVA, solo si figura expresamente o es "
            "un pago único. No es una penalización ni una fianza"
        ),
    )
    iva_incluido: bool | None = Field(
        default=None,
        description=(
            "True solo si el contrato da los importes únicamente con IVA incluido y no se "
            "puede separar la base. False si son sin IVA. Null si no hay importes"
        ),
    )
    currency: str = Field(default="EUR", description="ISO 4217, normalmente EUR")
    objeto: str | None = Field(
        default=None,
        description="Resumen corto del objeto del contrato (1-3 frases)",
    )
    confidence: float = Field(
        ge=0,
        le=1,
        description="Tu confianza global en la extracción (0=incierto, 1=seguro)",
    )

    @field_validator("fecha_firma", "fecha_inicio", "fecha_fin", mode="before")
    @classmethod
    def _coerce_fecha(cls, v: object) -> object:
        if isinstance(v, str):
            return date.fromisoformat(v)
        return v

    @field_validator("importe_periodico", "importe_total", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: object) -> object:
        if isinstance(v, int | float):
            return Decimal(str(v))
        return v

    @property
    def importe_anual(self) -> Decimal | None:
        """Importe anual equivalente (calculado, no lo devuelve el modelo)."""
        return annual_amount(self.importe_periodico, self.periodicidad)
