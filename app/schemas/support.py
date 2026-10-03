"""Schemas del formulario de soporte técnico (admin del tenant → SADM)."""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

SUPPORT_TITLE_MAX_LENGTH = 100
SUPPORT_MESSAGE_MAX_LENGTH = 2000


class SupportRequestKind(enum.StrEnum):
    ERROR = "error"
    QUESTION = "question"
    SUGGESTION = "suggestion"
    HELP = "help"


class SupportSeverity(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


SUPPORT_KIND_LABELS: dict[SupportRequestKind, str] = {
    SupportRequestKind.ERROR: "Error",
    SupportRequestKind.QUESTION: "Duda",
    SupportRequestKind.SUGGESTION: "Sugerencia",
    SupportRequestKind.HELP: "Ayuda",
}

SUPPORT_SEVERITY_LABELS: dict[SupportSeverity, str] = {
    SupportSeverity.LOW: "Baja",
    SupportSeverity.MEDIUM: "Media",
    SupportSeverity.HIGH: "Alta",
    SupportSeverity.CRITICAL: "Crítica",
}


class SupportRequestCreate(BaseModel):
    """Datos del formulario. El adjunto se valida aparte (app.core.support_uploads)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=SUPPORT_TITLE_MAX_LENGTH)
    message: str = Field(min_length=1, max_length=SUPPORT_MESSAGE_MAX_LENGTH)
    kind: SupportRequestKind
    severity: SupportSeverity

    @field_validator("title", mode="before")
    @classmethod
    def _single_line_title(cls, value: object) -> object:
        # Va al asunto del email: sin saltos de línea (evita inyección de cabeceras).
        return " ".join(value.split()) if isinstance(value, str) else value

    @field_validator("message", mode="before")
    @classmethod
    def _normalize_newlines(cls, value: object) -> object:
        # El navegador envía \r\n pero maxlength del textarea cuenta 1 carácter por salto.
        if isinstance(value, str):
            return value.replace("\r\n", "\n").replace("\r", "\n").strip()
        return value
