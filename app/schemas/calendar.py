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


class CalendarEventUpdate(BaseModel):
    """Payload para actualizar un evento existente."""

    summary: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=8000)
    start: str = Field(description="ISO 8601 dateTime o date (YYYY-MM-DD)")
    end: str = Field(description="ISO 8601 dateTime o date (YYYY-MM-DD)")


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
