"""Schemas de canales externos (Paso 21 E/F)."""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ChannelIntegrationStatus(enum.StrEnum):
    active = "active"
    revoked = "revoked"


class ChannelResponse(BaseModel):
    """Resultado de answer_for_channel: texto de respuesta y métricas de confianza."""

    text: str
    confidence: float
    citations_count: int


class ChannelIntegrationRead(BaseModel):
    """Representación pública de una integración de canal (sin tokens cifrados)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    channel: str
    phone_number_id: str | None
    display_name: str | None
    status: str
    confidence_threshold: float
    created_at: datetime
    updated_at: datetime
