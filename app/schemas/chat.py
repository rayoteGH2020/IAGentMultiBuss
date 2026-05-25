"""Schemas de lectura para chat documental y catálogo doc_types."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import ChatMessageRole
from app.schemas.pagination import Page


class DocTypeRead(BaseModel):
    """Proyección de un tipo de documento activo en catálogo."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    description: str | None = None
    is_active: bool = True


class ChatMessageRead(BaseModel):
    """Mensaje de hilo para UI y serialización de historial."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    thread_id: UUID
    tenant_id: UUID
    role: ChatMessageRole
    content: str | None = None
    tool_call: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None
    llm_call_id: UUID | None = None
    created_at: datetime


class ChatThreadRead(BaseModel):
    """Hilo de conversación con metadatos de listado."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    user_id: UUID | None = None
    title: str | None = None
    created_at: datetime
    updated_at: datetime


class ChatThreadListFilters(BaseModel):
    """Filtros para listar hilos del usuario en sidebar."""

    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ChatMessageListFilters(BaseModel):
    """Filtros para cargar historial de un hilo (truncado en servicio)."""

    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


# Re-export conveniente para tools y rutas
ChatThreadPage = Page[ChatThreadRead]
ChatMessagePage = Page[ChatMessageRead]
