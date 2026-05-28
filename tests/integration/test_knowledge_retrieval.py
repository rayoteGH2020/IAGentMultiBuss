"""Tests de integración: búsqueda híbrida RAG (Paso 19 Fase D)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from app.core.db import set_tenant_context
from app.models import Tenant
from app.models.knowledge import KnowledgeDocumentKind
from app.schemas.knowledge_search import KnowledgeSearchFilters
from app.services import knowledge_search_service as kss
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.knowledge_retrieval_helpers import (
    KnowledgeRetrievalSeed,
    ndcg_at_k,
    seed_knowledge_retrieval,
    unit_vector,
)

pytestmark = pytest.mark.integration


class _FakeLLMClient:
    """Cliente LLM que devuelve embeddings controlados por dimensión."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    async def embed(
        self,
        texts: list[str],
        *,
        tenant_id: UUID,
        db: AsyncSession,
    ) -> list[list[float]]:
        del texts, tenant_id, db
        return [self._vector[:]]


@asynccontextmanager
async def _noop_langfuse_obs(*_a: object, **_kw: object) -> AsyncIterator[MagicMock]:
    obs = MagicMock()
    yield obs


def _patch_search_deps(
    monkeypatch: pytest.MonkeyPatch,
    *,
    embed_vector: list[float],
) -> None:
    fake_langfuse = MagicMock()
    fake_langfuse.create_trace_id.return_value = uuid4()
    fake_obs = MagicMock()
    fake_langfuse.start_observation.return_value = fake_obs
    monkeypatch.setattr(kss, "get_langfuse", lambda: fake_langfuse)

    import app.services.audit_service as audit_mod

    async def _noop_audit(*_a: object, **_kw: object) -> None:
        pass

    monkeypatch.setattr(audit_mod, "log_action", _noop_audit)


@pytest.fixture
async def knowledge_retrieval_seed(
    knowledge_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> tuple[AsyncSession, Tenant, KnowledgeRetrievalSeed]:
    tenant = await tenant_factory(name="Retrieval Tenant")
    await set_tenant_context(db_session, str(tenant.id))
    seed = await seed_knowledge_retrieval(db_session, tenant_id=tenant.id)
    return db_session, tenant, seed


@pytest.mark.asyncio
async def test_dense_search_returns_relevant(
    knowledge_retrieval_seed: tuple[AsyncSession, Tenant, KnowledgeRetrievalSeed],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, tenant, seed = knowledge_retrieval_seed
    _patch_search_deps(monkeypatch, embed_vector=unit_vector(512, 0))
    llm = _FakeLLMClient(unit_vector(512, 0))

    result = await kss.search(
        db,
        tenant_id=tenant.id,
        query="horario de atención al cliente",
        filters=KnowledgeSearchFilters(top_k=3),
        llm_client=llm,  # type: ignore[arg-type]
        redis=None,
    )

    top_ids = [c.id for c in result.chunks[:3]]
    assert seed.schedule_chunk_id in top_ids


@pytest.mark.asyncio
async def test_sparse_search_exact_term(
    knowledge_retrieval_seed: tuple[AsyncSession, Tenant, KnowledgeRetrievalSeed],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, tenant, seed = knowledge_retrieval_seed
    _patch_search_deps(monkeypatch, embed_vector=unit_vector(512, 99))

    sparse_rows = await kss._sparse_search(
        db,
        tenant_id=tenant.id,
        query="CODIGOCONTRATO4242",
        filter_sql="",
        filter_params={},
        limit=10,
    )
    assert sparse_rows
    assert sparse_rows[0]["chunk_id"] == str(seed.contract_chunk_id)


@pytest.mark.asyncio
async def test_hybrid_beats_dense_alone(
    knowledge_retrieval_seed: tuple[AsyncSession, Tenant, KnowledgeRetrievalSeed],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, tenant, seed = knowledge_retrieval_seed
    _patch_search_deps(monkeypatch, embed_vector=unit_vector(512, 10))
    llm = _FakeLLMClient(unit_vector(512, 10))

    hybrid = await kss.search(
        db,
        tenant_id=tenant.id,
        query="ZETATERMINO789 horario",
        filters=KnowledgeSearchFilters(top_k=5),
        llm_client=llm,  # type: ignore[arg-type]
        redis=None,
    )

    dense_rows = await kss._dense_search(
        db,
        tenant_id=tenant.id,
        query_vector=unit_vector(512, 10),
        filter_sql="",
        filter_params={},
        limit=60,
    )
    dense_ids = [UUID(r["chunk_id"]) for r in dense_rows[:5]]

    hybrid_ids = [c.id for c in hybrid.chunks[:5]]
    ndcg_hybrid = ndcg_at_k(seed.hit_chunk_id, hybrid_ids, k=5)
    ndcg_dense = ndcg_at_k(seed.hit_chunk_id, dense_ids, k=5)
    assert ndcg_hybrid > ndcg_dense


@pytest.mark.asyncio
async def test_rls_isolation(
    knowledge_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_a = await tenant_factory(name="Tenant A RAG")
    tenant_b = await tenant_factory(name="Tenant B RAG")
    await set_tenant_context(db_session, str(tenant_a.id))
    seed_a = await seed_knowledge_retrieval(db_session, tenant_id=tenant_a.id)

    await set_tenant_context(db_session, str(tenant_b.id))
    _patch_search_deps(monkeypatch, embed_vector=unit_vector(512, 0))
    llm = _FakeLLMClient(unit_vector(512, 0))

    result = await kss.search(
        db_session,
        tenant_id=tenant_b.id,
        query="horario de atención",
        filters=KnowledgeSearchFilters(top_k=5),
        llm_client=llm,  # type: ignore[arg-type]
        redis=None,
    )

    returned_ids = {c.id for c in result.chunks}
    assert seed_a.schedule_chunk_id not in returned_ids


@pytest.mark.asyncio
async def test_kind_filter(
    knowledge_retrieval_seed: tuple[AsyncSession, Tenant, KnowledgeRetrievalSeed],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, tenant, seed = knowledge_retrieval_seed
    _patch_search_deps(monkeypatch, embed_vector=unit_vector(512, 1))
    llm = _FakeLLMClient(unit_vector(512, 1))

    result = await kss.search(
        db,
        tenant_id=tenant.id,
        query="CODIGOCONTRATO4242 pago",
        filters=KnowledgeSearchFilters(
            kind=[KnowledgeDocumentKind.contract],
            top_k=5,
        ),
        llm_client=llm,  # type: ignore[arg-type]
        redis=None,
    )

    assert result.chunks
    assert all(c.kind.value == "contract" for c in result.chunks)
    assert seed.contract_chunk_id in {c.id for c in result.chunks}
    assert seed.schedule_chunk_id not in {c.id for c in result.chunks}


@pytest.mark.asyncio
async def test_get_chunk_by_id_wrong_tenant(
    knowledge_retrieval_seed: tuple[AsyncSession, Tenant, KnowledgeRetrievalSeed],
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    db, _tenant_a, seed = knowledge_retrieval_seed
    tenant_b = await tenant_factory(name="Other tenant chunk")
    await set_tenant_context(db, str(tenant_b.id))

    chunk = await kss.get_chunk_by_id(
        db,
        tenant_id=tenant_b.id,
        chunk_id=seed.schedule_chunk_id,
    )
    assert chunk is None
