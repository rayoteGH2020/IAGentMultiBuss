"""Tests de integración para la ingesta de URLs de conocimiento (Paso 21 A).

Sigue el mismo patrón que test_knowledge_worker.py:
- Postgres real (tablas knowledge_documents + knowledge_chunks).
- Sin ARQ real: el job se llama directamente con contexto vacío.
- Sin URL real: httpx interceptado con respx.
- Sin R2 real: _FakeStorage que guarda y sirve los bytes en memoria.
- Sin Voyage AI real: _FakeLLMClient devuelve embeddings constantes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import pytest
import respx
from app.core.db import set_tenant_context
from app.core.keys import knowledge_url_key
from app.jobs import knowledge_url_jobs
from app.models import Tenant
from app.models.knowledge import KnowledgeDocument, KnowledgeDocumentKind, KnowledgeDocumentStatus
from httpx import Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

_FAKE_EMBEDDING = [1.0 / (512**0.5)] * 512

_HTML_CONTENT = (
    "<html>"
    "<head><title>Preguntas Frecuentes</title></head>"
    "<body>"
    "<h1>FAQ de la empresa</h1>"
    "<p>Abrimos de lunes a viernes de 9 a 18 horas.</p>"
    "<p>El precio del servicio básico es de 49 euros al mes.</p>"
    "<p>Para más información, contacta con nosotros.</p>"
    "</body>"
    "</html>"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def knowledge_url_schema_ready(db_session: AsyncSession) -> None:
    """Salta si faltan las tablas o la columna source_url (migración p21_a)."""
    for table in ("knowledge_documents", "knowledge_chunks"):
        row = await db_session.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                f"WHERE table_schema='public' AND table_name='{table}'"
            )
        )
        if row.scalar_one_or_none() is None:
            pytest.skip(f"Tabla '{table}' no existe. Ejecuta alembic upgrade head.")

    col_row = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='knowledge_documents' "
            "AND column_name='source_url'"
        )
    )
    if col_row.scalar_one_or_none() is None:
        pytest.skip("Columna source_url no existe. Ejecuta migración p21_a.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeStorage:
    """Storage en memoria: captura el upload del scraper y lo sirve al pipeline."""

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
# Test principal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_knowledge_url_full_flow(
    knowledge_url_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """URL → scraping → upload R2 → pipeline → status=ready, chunk_count > 0."""
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    # --- Mocks ---

    fake_storage = _FakeStorage()

    import app.core.web_scraper as ws_mod
    import app.llm.client as llm_mod
    import app.services.audit_service as audit_mod
    import app.services.knowledge_index_service as kis

    monkeypatch.setattr(kis, "get_storage", lambda: fake_storage)
    monkeypatch.setattr(ws_mod, "get_storage", lambda: fake_storage, raising=False)
    # El scraper usa get_storage dentro del job; parcheamos en knowledge_url_jobs también
    monkeypatch.setattr(knowledge_url_jobs, "get_storage", lambda: fake_storage)
    monkeypatch.setattr(llm_mod, "get_llm_client", lambda: _FakeLLMClient())

    async def _noop_log(*a: Any, **kw: Any) -> None:
        pass

    monkeypatch.setattr(audit_mod, "log_action", _noop_log)

    @asynccontextmanager
    async def _noop_slot(*_a: object, **_kw: object) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(knowledge_url_jobs, "tenant_knowledge_indexing_slot", _noop_slot)

    # --- Documento en BD ---
    source_url = "https://example.com/faq"
    key = knowledge_url_key(tenant.id)

    doc = KnowledgeDocument(
        tenant_id=tenant.id,
        kind=KnowledgeDocumentKind.faq,
        name=source_url,
        original_filename=source_url[:300],
        source_file_key=key,
        source_mime="text/plain",
        source_url=source_url,
        status=KnowledgeDocumentStatus.pending,
        chunk_count=0,
        file_size_bytes=0,
        uploaded_by=None,
    )
    db_session.add(doc)
    await db_session.commit()
    doc_id = doc.id
    tenant_id = tenant.id

    # --- Ejecutar job directamente (sin ARQ real), interceptando HTTP ---
    with respx.mock:
        respx.get("https://example.com/robots.txt").mock(return_value=Response(404))
        respx.get(source_url).mock(return_value=Response(200, text=_HTML_CONTENT))

        result = await knowledge_url_jobs.index_knowledge_url(
            {"redis": None},
            str(doc_id),
            str(tenant_id),
        )

    assert result["status"] == "ok", f"Job retornó: {result}"

    # --- Verificar BD ---
    await set_tenant_context(db_session, str(tenant_id))
    db_session.expire_all()

    updated = (
        await db_session.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id))
    ).scalar_one()

    assert updated.status == KnowledgeDocumentStatus.ready
    assert updated.chunk_count > 0
    assert updated.ingested_at is not None
    # El nombre debe haberse actualizado con el título de la página
    assert updated.name == "Preguntas Frecuentes"

    # Chunks en BD
    from app.models.knowledge import KnowledgeChunk

    chunks = (
        (
            await db_session.execute(
                select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc_id)
            )
        )
        .scalars()
        .all()
    )

    assert len(chunks) == updated.chunk_count
    assert all(c.content for c in chunks)


@pytest.mark.asyncio
async def test_index_knowledge_url_scraping_failure_marks_failed(
    knowledge_url_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si la URL devuelve HTTP 404, el documento queda en estado failed."""
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    import app.services.audit_service as audit_mod

    async def _noop_log(*a: Any, **kw: Any) -> None:
        pass

    monkeypatch.setattr(audit_mod, "log_action", _noop_log)

    @asynccontextmanager
    async def _noop_slot(*_a: object, **_kw: object) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(knowledge_url_jobs, "tenant_knowledge_indexing_slot", _noop_slot)

    source_url = "https://example.com/notfound"
    key = knowledge_url_key(tenant.id)

    doc = KnowledgeDocument(
        tenant_id=tenant.id,
        kind=KnowledgeDocumentKind.other,
        name=source_url,
        original_filename=source_url[:300],
        source_file_key=key,
        source_mime="text/plain",
        source_url=source_url,
        status=KnowledgeDocumentStatus.pending,
        chunk_count=0,
        file_size_bytes=0,
        uploaded_by=None,
    )
    db_session.add(doc)
    await db_session.commit()
    doc_id = doc.id

    with respx.mock:
        respx.get("https://example.com/robots.txt").mock(return_value=Response(404))
        respx.get(source_url).mock(return_value=Response(404))

        result = await knowledge_url_jobs.index_knowledge_url(
            {"redis": None},
            str(doc_id),
            str(tenant.id),
        )

    assert result["status"] == "failed"

    await set_tenant_context(db_session, str(tenant.id))
    db_session.expire_all()

    failed_doc = (
        await db_session.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id))
    ).scalar_one()

    assert failed_doc.status == KnowledgeDocumentStatus.failed
    assert failed_doc.error_message is not None
    assert "404" in failed_doc.error_message
