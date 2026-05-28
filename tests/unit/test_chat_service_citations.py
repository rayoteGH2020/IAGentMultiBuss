"""Tests unitarios de extracción de citas RAG en chat (Paso 20)."""

from __future__ import annotations

from uuid import uuid4

from app.config import get_settings
from app.schemas.chat import ChatCitation, ChatMessageRead
from app.services.chat_citations import (
    CITATION_SNIPPET_MAX_CHARS,
    citations_from_json,
    citations_to_json,
    extract_citations_from_search_data,
    extract_citations_from_tool_result,
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


def test_extract_citations_from_tool_result_wrapper() -> None:
    """API ``extract_citations_from_tool_result`` (payload de ToolResult.data)."""
    payload: dict[str, object] = {"chunks": [_chunk_payload(score=0.75)]}
    cites = extract_citations_from_tool_result(payload)
    assert len(cites) == 1
    assert cites[0].document_name == "Manual"


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


def test_citations_below_threshold_excluded() -> None:
    settings = get_settings()
    threshold = settings.knowledge_chat_min_score_threshold
    above = _chunk_payload(score=threshold + 0.1)
    below = _chunk_payload(score=max(0.0, threshold - 0.2))
    cites = extract_citations_from_search_data(
        {"chunks": [above, below]},
        settings=settings,
    )
    assert len(cites) == 1
    assert cites[0].score >= threshold


def test_citations_json_roundtrip() -> None:
    chunk_id = uuid4()
    doc_id = uuid4()
    original = [
        ChatCitation(
            ref=1,
            chunk_id=chunk_id,
            document_id=doc_id,
            document_name="Contrato Marco 2024",
            kind="contract",
            position=3,
            content_snippet="El horario de atención es de lunes a viernes",
            score=0.87,
        ),
    ]
    payload = citations_to_json(original)
    restored = citations_from_json(payload)
    assert len(restored) == 1
    assert restored[0].chunk_id == chunk_id
    assert restored[0].document_id == doc_id
    assert restored[0].document_name == "Contrato Marco 2024"
    assert restored[0].ref == 1


def test_chat_message_read_coerces_citations_from_json() -> None:
    chunk_id = uuid4()
    raw = {
        "id": uuid4(),
        "thread_id": uuid4(),
        "tenant_id": uuid4(),
        "role": "assistant",
        "content": "Respuesta",
        "citations": [
            {
                "ref": 1,
                "chunk_id": str(chunk_id),
                "document_id": str(uuid4()),
                "document_name": "FAQ",
                "kind": "faq",
                "position": 0,
                "content_snippet": "Texto",
                "score": 0.5,
            },
        ],
        "created_at": "2026-05-27T12:00:00+00:00",
    }
    msg = ChatMessageRead.model_validate(raw)
    assert msg.citations is not None
    assert len(msg.citations) == 1
    assert msg.citations[0].chunk_id == chunk_id


def test_content_snippet_truncated_to_max_chars() -> None:
    long_text = "x" * (CITATION_SNIPPET_MAX_CHARS + 50)
    cites = extract_citations_from_search_data(
        {"chunks": [_chunk_payload(score=0.9, content=long_text)]},
    )
    assert len(cites) == 1
    assert len(cites[0].content_snippet) == CITATION_SNIPPET_MAX_CHARS


def test_merge_citation_lists_dedupes_by_chunk_id() -> None:
    shared = uuid4()
    a = ChatCitation(
        ref=1,
        chunk_id=shared,
        document_name="A",
        kind="faq",
        position=0,
        content_snippet="a",
        score=0.4,
    )
    b = a.model_copy(update={"score": 0.9})
    merged = merge_citation_lists([a], [b])
    assert len(merged) == 1
    assert merged[0].score == 0.9


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
