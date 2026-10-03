"""DTOs de traza de chat para la consola SuperAdmin."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models import ChatMessageRole
from app.schemas.chat import ChatCitation


class LLMCallTraceRead(BaseModel):
    """Proyección de ``llm_calls`` anidada en un mensaje o en el detalle del hilo."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    task: str
    model: str
    provider: str
    prompt_version: str | None = None
    source_filename: str | None = None
    input_tokens: int
    output_tokens: int
    cost_eur: Decimal
    latency_ms: int
    status: str
    error: str | None = None
    langfuse_trace_id: str | None = None
    created_at: datetime


class ChatTraceAuditRead(BaseModel):
    """Evento de ``audit_log`` correlacionado por ``metadata.thread_id``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    user_id: UUID | None = None
    action: str
    resource_type: str
    resource_id: UUID | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime


class ChatTraceMessageRead(BaseModel):
    """Mensaje del hilo en orden de ``created_at``, con LLM anidado si existe."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    thread_id: UUID
    tenant_id: UUID
    role: ChatMessageRole
    content: str | None = None
    tool_call: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None
    citations: list[ChatCitation] | list[dict[str, Any]] | None = None
    llm_call_id: UUID | None = None
    created_at: datetime
    llm_call: LLMCallTraceRead | None = None


class ChatTraceThreadListItem(BaseModel):
    """Fila del listado cross-tenant de hilos."""

    id: UUID
    tenant_id: UUID
    tenant_name: str
    user_id: UUID | None = None
    user_email: str | None = None
    title: str | None = None
    is_hidden: bool = False
    message_count: int = 0
    created_at: datetime
    updated_at: datetime


class ChatTraceThreadDetail(BaseModel):
    """Detalle completo: hilo + mensajes ordenados + LLM + auditoría."""

    thread: ChatTraceThreadListItem
    messages: list[ChatTraceMessageRead]
    llm_calls: list[LLMCallTraceRead]
    audit_events: list[ChatTraceAuditRead]
