"""Tests de integración para la ingesta de FAQ manual (Paso 21 B.6).

Sigue el mismo patrón que test_knowledge_worker.py y test_knowledge_url.py:
- Postgres real (tablas knowledge_documents + knowledge_chunks).
- Sin ARQ real: el job index_knowledge_document se llama directamente.
- Sin R2 real: _FakeStorage en memoria.
- Sin Voyage AI real: _FakeLLMClient con embeddings constantes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import pytest
from app.core.db import set_tenant_context
from app.core.faq_serializer import FaqPair
from app.jobs import knowledge_jobs
from app.models import Tenant
from app.models.knowledge import KnowledgeDocumentKind, KnowledgeDocumentStatus
from app.services import knowledge_document_service
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

_FAKE_EMBEDDING = [1.0 / (512**0.5)] * 512

_FAQ_PAIRS = [
    FaqPair(
        question="¿Cuál es vuestro horario?", answer="Abrimos de lunes a viernes de 9 a 18 horas."
    ),
    FaqPair(question="¿Hacéis envíos?", answer="Sí, enviamos a toda España con 48 horas de plazo."),
    FaqPair(
        question="¿Aceptáis devoluciones?",
        answer="Sí, tienes 30 días desde la compra para devolverlo.",
    ),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def knowledge_faq_schema_ready(db_session: AsyncSession) -> None:
    """Salta si falta la columna faq_content (migración p21_a)."""
    for table in ("knowledge_documents", "knowledge_chunks"):
        row = await db_session.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                f"WHERE table_schema='public' AND table_name='{table}'"
            )
        )
        if row.scalar_one_or_none() is None:
            pytest.skip(f"Tabla '{table}' no existe. Ejecuta alembic upgrade head.")

    col = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='knowledge_documents' "
            "AND column_name='faq_content'"
        )
    )
    if col.scalar_one_or_none() is None:
        pytest.skip("Columna faq_content no existe. Ejecuta migración p21_a.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeStorage:
    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    async def upload_bytes(self, key: str, data: bytes, content_type: str = "") -> str:
        self._blobs[key] = data
        return key

    async def download_bytes(self, key: str) -> bytes:
        return self._blobs.get(key, b"")

    async def delete(self, key: str) -> None:
        self._blobs.pop(key, None)

    async def presigned_url_get(self, key: str, ttl: int = 3600) -> str:
        return f"http://fake-r2/{key}"


class _FakeLLMClient:
    async def embed(self, texts: list[str], *, tenant_id: Any, db: Any) -> list[list[float]]:
        return [_FAKE_EMBEDDING[:] for _ in texts]


# ---------------------------------------------------------------------------
# Test: flujo completo create_from_faq → index_knowledge_document → ready
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_faq_and_index_full_flow(
    knowledge_faq_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_from_faq → job → status=ready, chunk_count > 0, faq_content persistido."""
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    fake_storage = _FakeStorage()

    import app.llm.client as llm_mod
    import app.services.audit_service as audit_mod
    import app.services.knowledge_document_service as kds
    import app.services.knowledge_index_service as kis

    monkeypatch.setattr(kis, "get_storage", lambda: fake_storage)
    monkeypatch.setattr(kds, "get_storage", lambda: fake_storage)
    monkeypatch.setattr(llm_mod, "get_llm_client", lambda: _FakeLLMClient())

    async def _noop_log(*a: Any, **kw: Any) -> None:
        pass

    monkeypatch.setattr(audit_mod, "log_action", _noop_log)

    @asynccontextmanager
    async def _noop_slot(*_a: object, **_kw: object) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(knowledge_jobs, "tenant_knowledge_indexing_slot", _noop_slot)

    # Crear el FAQ (sube texto a R2 fake y crea doc en BD)
    doc = await knowledge_document_service.create_from_faq(
        db_session,
        tenant_id=tenant.id,
        user_id=None,
        pairs=_FAQ_PAIRS,
        kind=KnowledgeDocumentKind.faq,
        name="FAQ de prueba",
    )
    await db_session.commit()

    assert doc.faq_content is not None
    assert "P: ¿Cuál es vuestro horario?" in doc.faq_content
    assert doc.status == KnowledgeDocumentStatus.pending

    doc_id = doc.id
    tenant_id = tenant.id

    # Ejecutar el job de indexación directamente
    result = await knowledge_jobs.index_knowledge_document(
        {"redis": None},
        str(doc_id),
        str(tenant_id),
    )

    assert result["status"] == "ok", f"Job retornó: {result}"

    # Verificar BD
    await set_tenant_context(db_session, str(tenant_id))
    db_session.expire_all()

    refreshed = await knowledge_document_service.get_document(
        db_session, tenant_id=tenant_id, document_id=doc_id, include_download_url=False
    )
    assert refreshed.status == KnowledgeDocumentStatus.ready
    assert refreshed.chunk_count > 0


# ---------------------------------------------------------------------------
# Test: serialize/deserialize round-trip en BD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_faq_pairs_roundtrip(
    knowledge_faq_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Los pares Q/A se recuperan fielmente tras create_from_faq."""
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    fake_storage = _FakeStorage()

    import app.services.audit_service as audit_mod
    import app.services.knowledge_document_service as kds

    monkeypatch.setattr(kds, "get_storage", lambda: fake_storage)

    async def _noop_log(*a: Any, **kw: Any) -> None:
        pass

    monkeypatch.setattr(audit_mod, "log_action", _noop_log)

    doc = await knowledge_document_service.create_from_faq(
        db_session,
        tenant_id=tenant.id,
        user_id=None,
        pairs=_FAQ_PAIRS,
        kind=KnowledgeDocumentKind.faq,
    )
    await db_session.flush()

    # Recargar el ORM y parsear pares
    from app.models.knowledge import KnowledgeDocument

    reloaded = (
        await db_session.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == doc.id))
    ).scalar_one()

    recovered = knowledge_document_service.get_faq_pairs(reloaded)
    assert len(recovered) == len(_FAQ_PAIRS)
    for orig, rec in zip(_FAQ_PAIRS, recovered, strict=True):
        assert rec.question == orig.question
        assert rec.answer == orig.answer
