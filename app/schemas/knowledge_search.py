"""Schemas de búsqueda y retrieval del módulo de conocimiento RAG (Paso 19).

Tres schemas de salida y uno de filtros de entrada:
- KnowledgeChunkRef   — fragmento individual devuelto por el retriever.
- KnowledgeSearchResult — respuesta completa de una búsqueda.
- KnowledgeSourceRef  — referencia ligera a un documento (para list_knowledge_sources).
- KnowledgeSearchFilters — parámetros de entrada para search().

INVARIANTE: ningún schema de este módulo expone el campo `embedding`.
El vector es interno de Postgres y nunca debe llegar al LLM ni a la UI.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.knowledge import KnowledgeDocumentKind


class KnowledgeChunkRef(BaseModel):
    """Fragmento de conocimiento tal como se devuelve al LLM o a la UI.

    El campo `embedding` está deliberadamente ausente: los vectores no deben
    aparecer en respuestas al modelo ni en logs (volumen + privacidad).
    """

    model_config = ConfigDict(from_attributes=False)

    id: UUID
    document_id: UUID
    document_name: str
    kind: KnowledgeDocumentKind
    position: int
    content: str
    # context: línea de contexto generada en Contextual Retrieval (opcional).
    # Es None cuando knowledge_contextual_retrieval_enabled=False (por defecto).
    context: str | None = None
    # metadata: diccionario libre del chunk (token_estimate, char_start, etc.).
    metadata: dict[str, Any] = Field(default_factory=dict)
    # score: RRF score combinado (dense + sparse). Rango teórico: 0 < score ≤ 2/61.
    # No es una probabilidad; solo sirve para ordenar por relevancia relativa.
    score: float


class KnowledgeSearchResult(BaseModel):
    """Respuesta completa de knowledge_search_service.search().

    Incluye metadatos de rendimiento (latency_ms) para evals y observabilidad.
    """

    model_config = ConfigDict(from_attributes=False)

    query: str
    total_found: int = Field(ge=0, description="Chunks tras merge RRF, antes de top_k")
    chunks: list[KnowledgeChunkRef]
    latency_ms: int = Field(ge=0, description="Tiempo total: embed + 2 queries + merge")


class KnowledgeSourceRef(BaseModel):
    """Vista ligera de un documento indexado, para la tool list_knowledge_sources.

    Solo los campos relevantes para que el LLM decida en qué documentos buscar.
    No incluye source_file_key, tenant_id ni ningún campo de infraestructura.
    """

    model_config = ConfigDict(from_attributes=False)

    id: UUID
    name: str
    kind: KnowledgeDocumentKind
    chunk_count: int = Field(ge=0)
    # ingested_at como cadena ISO para serialización limpia en tool response.
    ingested_at: str | None = None


class KnowledgeSearchFilters(BaseModel):
    """Parámetros de entrada para knowledge_search_service.search().

    Todos los campos son opcionales; los valores por defecto dan un comportamiento
    razonable sin configuración explícita.
    """

    model_config = ConfigDict(strict=False)

    # Filtrar solo por documentos de estas categorías. None = todas.
    kind: list[KnowledgeDocumentKind] | None = None
    # Filtrar solo por estos document_ids concretos. None = todos.
    document_ids: list[UUID] | None = None
    # Número de chunks a devolver tras el merge RRF.
    # Se clipa a [1, knowledge_max_top_k] en el servicio.
    top_k: int = Field(default=10, ge=1, le=25)

    @field_validator("kind", mode="before")
    @classmethod
    def _coerce_kind_strings(
        cls, v: list[str | KnowledgeDocumentKind] | None
    ) -> list[KnowledgeDocumentKind] | None:
        """Acepta strings además de enums para flexibilidad en llamadas desde tools."""
        if v is None:
            return None
        return [KnowledgeDocumentKind(k) if isinstance(k, str) else k for k in v]
