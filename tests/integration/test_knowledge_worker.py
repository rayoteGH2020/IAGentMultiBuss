"""Tests de integración para el worker de indexación de conocimiento (Paso 18).

Verifica el flujo completo desde documento en estado pending hasta ready con
chunks en BD, usando Postgres real pero mockeando R2 y Voyage AI para evitar
dependencias externas en CI.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import pytest
from app.core.db import set_tenant_context
from app.core.storage import reset_storage_for_tests
from app.jobs import knowledge_jobs
from app.models import Tenant
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentKind,
    KnowledgeDocumentStatus,
)
from app.services import knowledge_document_service
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

_TXT_BYTES = (
    b"Contrato de prestaci\xc3\xb3n de servicios.\n\n"
    b"Cl\xc3\xa1usula 1: el proveedor ofrece soporte t\xc3\xa9cnico.\n\n"
    b"Cl\xc3\xa1usula 2: el servicio est\xc3\xa1 disponible 24/7.\n\n"
    b"Cl\xc3\xa1usula 3: el precio mensual es de 99 EUR m\xc3\xa1s IVA."
)

# Embedding falso: lista de 512 floats (voyage-3-lite), normalizada en L2.
_FAKE_EMBEDDING = [1.0 / (512**0.5)] * 512


class _FakeStorage:
    def __init__(self, blob: bytes) -> None:
        self._blob = blob

    async def upload_bytes(self, key: str, data: bytes, content_type: str = "") -> str:
        return key

    async def download_bytes(self, key: str) -> bytes:
        return self._blob

    async def delete(self, key: str) -> None:
        pass

    async def presigned_url_get(self, key: str, ttl: int = 3600) -> str:
        return f"http://fake-r2/{key}"


@pytest.mark.asyncio
async def test_index_knowledge_document_worker_full_flow(
    knowledge_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Documento pending → worker ejecuta pipeline → status=ready, chunks > 0."""
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    # Parchear storage en knowledge_index_service, que es quien llama a get_storage.
    # knowledge_jobs no importa get_storage directamente (solo delega en run_index_pipeline).
    fake_storage = _FakeStorage(_TXT_BYTES)
    import app.services.knowledge_index_service as kis

    monkeypatch.setattr(kis, "get_storage", lambda: fake_storage)

    class _FakeLLMClient:
        async def embed(self, texts: list[str], *, tenant_id: Any, db: Any) -> list[list[float]]:
            return [_FAKE_EMBEDDING[:] for _ in texts]

    monkeypatch.setattr(
        "app.services.knowledge_index_service.get_llm_client",
        lambda: _FakeLLMClient(),
    )

    # Saltarse el semáforo Redis.
    @asynccontextmanager
    async def _noop_slot(*_a: object, **_kw: object) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(knowledge_jobs, "tenant_knowledge_indexing_slot", _noop_slot)

    # También parchear audit_service para evitar errores de tabla audit_log.
    import app.services.audit_service as audit_mod

    async def _noop_log(*a: Any, **kw: Any) -> None:
        pass

    monkeypatch.setattr(audit_mod, "log_action", _noop_log)

    # Insertar documento en BD (status=pending, sin upload real a R2).
    doc = KnowledgeDocument(
        tenant_id=tenant.id,
        kind=KnowledgeDocumentKind.faq,
        name="Contrato de prueba",
        original_filename="contrato.txt",
        source_file_key=f"tenants/{tenant.id}/docs/contrato.txt",
        source_mime="text/plain",
        status=KnowledgeDocumentStatus.pending,
        chunk_count=0,
        file_size_bytes=len(_TXT_BYTES),
        uploaded_by=None,
    )
    db_session.add(doc)
    await db_session.commit()

    doc_id = doc.id
    tenant_id = tenant.id

    # Ejecutar el worker directamente (sin ARQ real).
    result = await knowledge_jobs.index_knowledge_document({}, str(doc_id), str(tenant_id))

    assert result["status"] == "ok", f"Worker retornó: {result}"

    # Verificar estado en BD.
    await set_tenant_context(db_session, str(tenant_id))
    db_session.expire_all()

    refreshed = await knowledge_document_service.get_document(
        db_session, tenant_id=tenant_id, document_id=doc_id, include_download_url=False
    )
    assert refreshed.status == KnowledgeDocumentStatus.ready
    assert refreshed.chunk_count > 0
    assert refreshed.ingested_at is not None

    # Verificar que existen chunks en BD.
    chunks = (
        (
            await db_session.execute(
                select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(chunks) == refreshed.chunk_count
    assert all(c.content for c in chunks)

    reset_storage_for_tests()
