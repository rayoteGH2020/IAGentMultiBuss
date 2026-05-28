"""Tests unitarios de knowledge_search_service (Paso 19 Fase D)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.config import get_settings
from app.core.errors import RateLimitError, ValidationError
from app.schemas.knowledge_search import KnowledgeSearchFilters
from app.services import knowledge_search_service as kss


def _row(chunk_id: str, rank: int) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "document_id": str(uuid4()),
        "document_name": "Doc",
        "kind": "faq",
        "position": 0,
        "content": f"content-{chunk_id}",
        "context": None,
        "metadata": {},
        "rank": rank,
    }


def test_rrf_merge_both_lists() -> None:
    dense = [_row("a", 1), _row("b", 2)]
    sparse = [_row("b", 1), _row("c", 2)]
    merged = kss._rrf_merge(dense, sparse, k=60)

    scores = {r["chunk_id"]: r["rrf_score"] for r in merged}
    # b aparece en ambas listas → mayor score que a o c (solo una lista).
    assert scores["b"] > scores["a"]
    assert scores["b"] > scores["c"]
    assert [r["chunk_id"] for r in merged] == ["b", "a", "c"]


def test_rrf_merge_only_dense() -> None:
    dense = [_row("solo", 3)]
    merged = kss._rrf_merge(dense, [], k=60)
    assert len(merged) == 1
    assert merged[0]["chunk_id"] == "solo"
    assert merged[0]["rrf_score"] == pytest.approx(1.0 / (60 + 3))


def test_rrf_merge_empty_sparse() -> None:
    dense = [_row("x", 1), _row("y", 2)]
    merged = kss._rrf_merge(dense, [], k=60)
    assert [r["chunk_id"] for r in merged] == ["x", "y"]


@pytest.mark.asyncio
async def test_search_validates_empty_query() -> None:
    db = AsyncMock()
    llm = MagicMock()
    with pytest.raises(ValidationError, match="vacía"):
        await kss.search(
            db,
            tenant_id=uuid4(),
            query="   ",
            filters=KnowledgeSearchFilters(),
            llm_client=llm,
            redis=None,
        )


@pytest.mark.asyncio
async def test_search_caps_top_k() -> None:
    tenant_id = uuid4()
    chunk_id = str(uuid4())
    dense_row = _row(chunk_id, 1)
    dense_row["rrf_score"] = 0.5

    db = AsyncMock()
    llm = AsyncMock()
    llm.embed = AsyncMock(return_value=[[0.1] * 512])

    fake_obs = MagicMock()
    fake_langfuse = MagicMock()
    fake_langfuse.create_trace_id.return_value = uuid4()
    fake_langfuse.start_observation.return_value = fake_obs

    settings = get_settings()
    mock_settings = MagicMock()
    mock_settings.knowledge_max_top_k = 3
    mock_settings.knowledge_rrf_k = settings.knowledge_rrf_k
    mock_settings.knowledge_dense_candidates = settings.knowledge_dense_candidates
    mock_settings.knowledge_sparse_candidates = settings.knowledge_sparse_candidates
    mock_settings.knowledge_search_rpm_limit = settings.knowledge_search_rpm_limit

    with (
        patch.object(kss, "get_settings", return_value=mock_settings),
        patch.object(kss, "get_langfuse", return_value=fake_langfuse),
        patch.object(kss, "_dense_search", AsyncMock(return_value=[dense_row])),
        patch.object(kss, "_sparse_search", AsyncMock(return_value=[])),
        patch.object(kss.audit_service, "log_action", AsyncMock()),  # type: ignore[attr-defined]
    ):
        result = await kss.search(
            db,
            tenant_id=tenant_id,
            query="horario de atención",
            filters=KnowledgeSearchFilters(top_k=10),
            llm_client=llm,
            redis=None,
        )

    assert len(result.chunks) <= mock_settings.knowledge_max_top_k
    assert len(result.chunks) == 1


# ---------------------------------------------------------------------------
# _check_search_rate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_search_rate_happy_path() -> None:
    """Bajo el límite: incr llamado, sin excepción."""
    redis = AsyncMock()
    redis.incr.return_value = 5

    await kss._check_search_rate(redis, tenant_id=uuid4(), limit=120)

    redis.incr.assert_awaited_once()
    redis.decr.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_search_rate_sets_ttl_on_first_call() -> None:
    """Primer incremento (count == 1): expire debe llamarse con el TTL correcto."""
    redis = AsyncMock()
    redis.incr.return_value = 1

    await kss._check_search_rate(redis, tenant_id=uuid4(), limit=120)

    redis.expire.assert_awaited_once_with(
        redis.incr.call_args[0][0],  # mismo key que recibió incr
        kss._SEARCH_RATE_TTL_SECONDS,
    )


@pytest.mark.asyncio
async def test_check_search_rate_raises_when_limit_exceeded() -> None:
    """Supera el límite: decr revertido y RateLimitError lanzado."""
    redis = AsyncMock()
    redis.incr.return_value = 121

    with pytest.raises(RateLimitError):
        await kss._check_search_rate(redis, tenant_id=uuid4(), limit=120)

    redis.decr.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_calls_rate_check_when_redis_provided() -> None:
    """Con redis != None, el rate check se invoca antes de ejecutar la búsqueda."""
    tenant_id = uuid4()
    redis = AsyncMock()
    redis.incr.return_value = 1

    db = AsyncMock()
    llm = AsyncMock()
    llm.embed = AsyncMock(return_value=[[0.1] * 512])

    fake_obs = MagicMock()
    fake_langfuse = MagicMock()
    fake_langfuse.create_trace_id.return_value = uuid4()
    fake_langfuse.start_observation.return_value = fake_obs

    with (
        patch.object(kss, "get_langfuse", return_value=fake_langfuse),
        patch.object(kss, "_dense_search", AsyncMock(return_value=[])),
        patch.object(kss, "_sparse_search", AsyncMock(return_value=[])),
        patch.object(kss.audit_service, "log_action", AsyncMock()),  # type: ignore[attr-defined]
    ):
        await kss.search(
            db,
            tenant_id=tenant_id,
            query="horario",
            filters=KnowledgeSearchFilters(),
            llm_client=llm,
            redis=redis,
        )

    redis.incr.assert_awaited_once()
