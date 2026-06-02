"""Schemas Pydantic para Google Calendar OAuth e integración (Paso 17)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TokenResponse(BaseModel):
    """Respuesta del endpoint OAuth token de Google."""

    access_token: str
    refresh_token: str | None = None
    expires_in: int
    token_type: str = "Bearer"
    scope: str | None = None


class CalendarEvent(BaseModel):
    """Evento de calendario normalizado para la aplicación."""

    id: str
    summary: str | None = None
    description: str | None = None
    start: str
    end: str
    html_link: str | None = None


class CalendarEventCreate(BaseModel):
    """Payload para crear un evento en Google Calendar."""

    summary: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=8000)
    start: str = Field(description="ISO 8601 dateTime o date (YYYY-MM-DD)")
    end: str = Field(description="ISO 8601 dateTime o date (YYYY-MM-DD)")
    # None → Google aplica los recordatorios por defecto del calendario.
    # Para crear con recordatorios explícitos (p. ej. eventos de voz), pasar
    # lista de dicts {"method": "popup"|"email", "minutes": int}.
    reminders: list[dict[str, object]] | None = None


class CalendarEventUpdate(BaseModel):
    """Payload para actualizar un evento existente."""

    summary: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=8000)
    start: str = Field(description="ISO 8601 dateTime o date (YYYY-MM-DD)")
    end: str = Field(description="ISO 8601 dateTime o date (YYYY-MM-DD)")


# ---------------------------------------------------------------------------
# Voz → Google Calendar (Paso 23)
# ---------------------------------------------------------------------------


class _VoiceEventExtraction(BaseModel):
    """Schema exclusivo para Instructor en draft_event_from_transcript().

    No contiene `transcript`: el LLM extrae estructura a partir del texto
    dictado, pero no debe ecoar su propia entrada en la salida (innecesario
    y costoso en tokens). El servicio asigna `transcript` en VoiceEventDraft.
    No exponer fuera de app/llm/.
    """

    summary: str = Field(min_length=1, max_length=500, description="Título del evento")
    description: str | None = Field(default=None, max_length=8000)
    start: str = Field(description="ISO 8601 dateTime con offset, o date YYYY-MM-DD")
    end: str = Field(description="ISO 8601 dateTime con offset, o date YYYY-MM-DD")
    all_day: bool = False
    confidence: float = Field(ge=0, le=1, description="Confianza global de la interpretación")
    needs_clarification: bool = Field(
        default=False,
        description="True si falta información esencial (fecha/hora ambigua o ausente)",
    )
    clarification_reason: str | None = None


class VoiceEventDraft(BaseModel):
    """Borrador de evento listo para la pantalla de confirmación.

    `transcript` es asignado por voice_event_service tras llamar a
    draft_event_from_transcript(); no forma parte de la extracción LLM.
    """

    transcript: str
    summary: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=8000)
    start: str
    end: str
    all_day: bool = False
    confidence: float = Field(ge=0, le=1)
    needs_clarification: bool = False
    clarification_reason: str | None = None

    def to_event_create(self) -> CalendarEventCreate:
        """Convierte el borrador a CalendarEventCreate sin reminders.

        Los recordatorios los inyecta voice_event_service.confirm_event()
        para garantizar el invariante del objetivo #5 independientemente
        de lo que llegue del formulario de confirmación.
        """
        return CalendarEventCreate(
            summary=self.summary,
            description=self.description,
            start=self.start,
            end=self.end,
        )


class CalendarIntegrationRead(BaseModel):
    """Proyección de integración para UI (sin tokens)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    google_email: str | None
    google_calendar_id: str
    scopes: str | None
    created_at: datetime
    updated_at: datetime
