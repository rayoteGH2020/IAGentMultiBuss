"""Schemas Pydantic del módulo de base de conocimiento (Paso 18)."""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeDocumentStatus(enum.StrEnum):
    pending = "pending"
    indexing = "indexing"
    ready = "ready"
    failed = "failed"


class KnowledgeDocumentKind(enum.StrEnum):
    contract = "contract"
    terms = "terms"
    schedule = "schedule"
    services = "services"
    policy = "policy"
    faq = "faq"
    manual = "manual"
    other = "other"


class KnowledgeDocumentCreate(BaseModel):
    """Input para crear un documento de conocimiento desde un upload."""

    model_config = ConfigDict(strict=False)

    kind: KnowledgeDocumentKind
    name: str = Field(max_length=300)


class KnowledgeDocumentRead(BaseModel):
    """Representación de un documento de conocimiento para la UI."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    kind: KnowledgeDocumentKind
    name: str
    original_filename: str
    source_file_key: str
    source_mime: str
    status: KnowledgeDocumentStatus
    chunk_count: int
    error_message: str | None
    file_size_bytes: int
    uploaded_by: UUID | None
    ingested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    faq_content: str | None = None
    # Solo se rellena en get_document(); None en listados.
    download_url: str | None = None


class KnowledgeChunkRead(BaseModel):
    """Fragmento de chunk sin embedding (el vector nunca se expone a la UI)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    content: str
    context: str | None
    position: int


class KnowledgeDocumentFilters(BaseModel):
    """Filtros opcionales para list_documents()."""

    model_config = ConfigDict(strict=False)

    kind: KnowledgeDocumentKind | None = None
    status: KnowledgeDocumentStatus | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
