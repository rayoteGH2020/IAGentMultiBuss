"""Schema de clasificación de tipo documental (Instructor / LLM)."""

from __future__ import annotations

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
