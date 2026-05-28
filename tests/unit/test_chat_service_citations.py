"""Tests unitarios de extracción de citas RAG en chat (Paso 20)."""

from __future__ import annotations

from uuid import uuid4

from app.config import get_settings
from app.schemas.chat import ChatCitation
from app.services.chat_citations import (
    extract_citations_from_search_data,
    finalize_citations,
    merge_citation_lists,
)


def _chunk_payload(*, score: float = 0.5, content: str = "texto") -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "document_id": str(uuid4()),
        "source_name": "Manual",
        "kind": "policy",
        "position": 1,
        "content": content,
        "score": score,
    }


def test_extract_citations_from_tool_results() -> None:
    data = {"chunks": [_chunk_payload(score=0.9), _chunk_payload(score=0.1)]}
    cites = extract_citations_from_search_data(data)
    assert len(cites) == 1
    assert cites[0].score == 0.9
    assert "embedding" not in cites[0].model_dump()


def test_citations_sorted_by_score() -> None:
    low = ChatCitation(
        ref=1,
        chunk_id=uuid4(),
        document_name="A",
        kind="faq",
        position=0,
        content_snippet="a",
        score=0.4,
    )
    high = low.model_copy(
        update={"chunk_id": uuid4(), "document_name": "B", "score": 0.95},
    )
    result = finalize_citations([low, high])
    assert result[0].score >= result[-1].score
    assert result[0].document_name == "B"


def test_citations_capped_at_max() -> None:
    settings = get_settings()
    cites = [
        ChatCitation(
            ref=1,
            chunk_id=uuid4(),
            document_name=f"D{i}",
            kind="faq",
            position=i,
            content_snippet="x",
            score=0.5 + i * 0.01,
        )
        for i in range(10)
    ]
    merged = merge_citation_lists(cites, settings=settings)
    assert len(merged) <= settings.knowledge_chat_max_citations
