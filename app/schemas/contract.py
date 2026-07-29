"""Schema Pydantic de contrato extraído (módulo 1)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    fecha_inicio: date = Field(description="Fecha de inicio o firma del contrato")
    fecha_fin: date | None = Field(
        default=None,
        description="Fecha de fin o vencimiento si aparece",
    )
    importe: Decimal | None = Field(
        default=None,
        ge=0,
        description="Importe, canon o valor económico si aparece",
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

    @field_validator("fecha_inicio", "fecha_fin", mode="before")
    @classmethod
    def _coerce_fecha(cls, v: object) -> object:
        if isinstance(v, str):
            return date.fromisoformat(v)
        return v

    @field_validator("importe", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: object) -> object:
        if isinstance(v, int | float):
            return Decimal(str(v))
        return v
