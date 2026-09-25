"""Schema Pydantic de póliza de seguro extraída (módulo 1)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SeguroPoliza(BaseModel):
    model_config = ConfigDict(strict=False)

    aseguradora: str = Field(description="Nombre de la compañía aseguradora")
    numero_poliza: str | None = Field(
        default=None,
        description="Número de póliza si aparece",
    )
    tomador: str = Field(description="Nombre del tomador o asegurado principal")
    cif_nif: str | None = Field(
        default=None,
        description="CIF/NIF del tomador si aparece",
    )
    tipo_seguro: str | None = Field(
        default=None,
        description="Tipo de seguro (hogar, RC, flota, salud, etc.) si aparece",
    )
    fecha_inicio: date = Field(description="Inicio de vigencia de la póliza")
    fecha_fin: date | None = Field(
        default=None,
        description="Fin de vigencia si aparece",
    )
    prima: Decimal | None = Field(
        default=None,
        ge=0,
        description="Prima o importe a pagar si aparece",
    )
    currency: str = Field(default="EUR", description="ISO 4217, normalmente EUR")
    cobertura: str | None = Field(
        default=None,
        description="Resumen corto de coberturas (1-3 frases)",
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

    @field_validator("prima", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: object) -> object:
        if isinstance(v, int | float):
            return Decimal(str(v))
        return v
