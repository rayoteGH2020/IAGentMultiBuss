"""Schema de clasificación de tipo documental (Instructor / LLM)."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

DocTypeLiteral = Literal["factura", "ticket"]


class DocumentTypeClassification(BaseModel):
    doc_type: DocTypeLiteral = Field(
        description="Tipo de documento administrativo detectado",
    )
    confidence: float = Field(
        ge=0,
        le=1,
        description="Confianza en la clasificación (0=incierto, 1=seguro)",
    )
    reason: str = Field(
        description="Breve justificación basada en el contenido visible",
    )
    fecha_emision: date | None = Field(
        default=None,
        description=(
            "Fecha de emisión del documento (no la de vencimiento, pago ni servicio). "
            "null si no se lee con claridad"
        ),
    )
    fecha_confianza: float = Field(
        default=0,
        ge=0,
        le=1,
        description="Confianza en que fecha_emision es correcta y es la de emisión",
    )
