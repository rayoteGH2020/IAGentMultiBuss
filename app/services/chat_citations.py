"""Extracción y normalización de citas RAG para mensajes de chat (Paso 20)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.config import Settings, get_settings
from app.schemas.chat import ChatCitation


def extract_citations_from_search_data(
    data: dict[str, object],
    *,
    settings: Settings | None = None,
) -> list[ChatCitation]:
    """Convierte chunks de ``search_knowledge`` en citas normalizadas."""
    s = settings or get_settings()
    threshold = s.knowledge_chat_min_score_threshold
    raw_chunks = data.get("chunks")
    if not isinstance(raw_chunks, list):
        return []

    candidates: list[ChatCitation] = []
    for item in raw_chunks:
        if not isinstance(item, dict):
            continue
        score_raw = item.get("score", 0)
        try:
            score = float(score_raw)
        except (TypeError, ValueError):
            score = 0.0
        if score < threshold:
            continue
        chunk_id = item.get("id")
        if chunk_id is None:
            continue
        content = str(item.get("content", ""))
        document_id = item.get("document_id")
        candidates.append(
            ChatCitation(
                ref=1,
                chunk_id=UUID(str(chunk_id)),
                document_id=UUID(str(document_id)) if document_id else None,
                document_name=str(item.get("source_name", "Documento")),
                kind=str(item.get("kind", "other")),
                position=int(item.get("position", 0)),
                content_snippet=content[:200],
                score=score,
            ),
        )

    return finalize_citations(candidates, settings=s)


def extract_citations_from_tool_result(
    tool_result_data: dict[str, object],
    *,
    settings: Settings | None = None,
) -> list[ChatCitation]:
    """Extrae citas del payload ``data`` de un ``ToolResult`` de search_knowledge."""
    return extract_citations_from_search_data(tool_result_data, settings=settings)


def merge_citation_lists(
    *lists: list[ChatCitation],
    settings: Settings | None = None,
) -> list[ChatCitation]:
    """Fusiona listas de citas deduplicando por chunk_id (mayor score gana)."""
    by_chunk: dict[str, ChatCitation] = {}
    for citations in lists:
        for cite in citations:
            key = str(cite.chunk_id)
            existing = by_chunk.get(key)
            if existing is None or cite.score > existing.score:
                by_chunk[key] = cite
    return finalize_citations(list(by_chunk.values()), settings=settings)


def finalize_citations(
    citations: list[ChatCitation],
    *,
    settings: Settings | None = None,
) -> list[ChatCitation]:
    """Ordena por score, asigna ref 1..N y aplica tope máximo."""
    s = settings or get_settings()
    sorted_cites = sorted(citations, key=lambda c: c.score, reverse=True)
    capped = sorted_cites[: s.knowledge_chat_max_citations]
    return [cite.model_copy(update={"ref": i + 1}) for i, cite in enumerate(capped)]


def citations_to_json(citations: list[ChatCitation]) -> list[dict[str, Any]]:
    """Serializa citas para persistencia en ``chat_messages.citations``."""
    return [c.model_dump(mode="json") for c in citations]
