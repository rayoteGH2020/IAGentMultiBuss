"""Tests de integración: RLS de knowledge_documents y scanned PDF (Paso 18).

Verifica que:
  1. El tenant A no puede ver ni acceder a documentos del tenant B.
  2. Un PDF escaneado (sin capa de texto) termina en estado failed con mensaje claro.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.core.db import set_tenant_context
from app.core.errors import NotFoundError
from app.core.storage import reset_storage_for_tests
from app.jobs import knowledge_jobs
from app.models import Tenant
from app.models.knowledge import KnowledgeDocument, KnowledgeDocumentKind, KnowledgeDocumentStatus
from app.services import knowledge_document_service
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

_FAKE_EMBEDDING = [1.0 / (1536**0.5)] * 1536

# Bytes de un "PDF" escaneado simulado: cabecera PDF válida pero pypdf no
# puede extraer texto (todas las páginas devuelven cadena vacía).
_SCANNED_PDF_BYTES = b"%PDF-1.4 fake-scanned"


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


@asynccontextmanager
async def _noop_slot(*_a: object, **_kw: object) -> AsyncIterator[None]:
    yield


async def _noop_audit(*a: Any, **kw: Any) -> None:
    pass


# ---------------------------------------------------------------------------
# Test 1: aislamiento RLS — el tenant B no ve documentos del tenant A
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rls_tenant_isolation(
    knowledge_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant_a: Tenant = await tenant_factory(name="Tenant A")
    tenant_b: Tenant = await tenant_factory(name="Tenant B")

    await set_tenant_context(db_session, str(tenant_a.id))

    # Insertar un documento bajo el tenant A.
    doc_a = KnowledgeDocument(
        tenant_id=tenant_a.id,
        kind=KnowledgeDocumentKind.other,
        name="Secreto de A",
        original_filename="secreto.txt",
        source_file_key=f"tenants/{tenant_a.id}/docs/secreto.txt",
        source_mime="text/plain",
        status=KnowledgeDocumentStatus.pending,
        chunk_count=0,
        file_size_bytes=100,
        uploaded_by=None,
    )
    db_session.add(doc_a)
    await db_session.commit()

    doc_a_id = doc_a.id

    # Cambiar contexto al tenant B: RLS no debe ver el documento del tenant A.
    await set_tenant_context(db_session, str(tenant_b.id))

    # get_document lanza NotFoundError si RLS filtra correctamente.
    with pytest.raises(NotFoundError):
        await knowledge_document_service.get_document(
            db_session,
            tenant_id=tenant_b.id,
            document_id=doc_a_id,
            include_download_url=False,
        )

    # Verificar también con query directa que la tabla está filtrada.
    count_result = await db_session.execute(
        select(func.count()).select_from(
            select(KnowledgeDocument).where(KnowledgeDocument.id == doc_a_id).subquery()
        )
    )
    visible = count_result.scalar_one()
    assert visible == 0, "RLS no filtró el documento del tenant A para el tenant B"


# ---------------------------------------------------------------------------
# Test 2: PDF escaneado → worker marca el documento como failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scanned_pdf_marks_document_failed(
    knowledge_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    # Storage falso que devuelve el "PDF escaneado".
    fake_storage = _FakeStorage(_SCANNED_PDF_BYTES)
    monkeypatch.setattr(knowledge_jobs, "get_storage", lambda: fake_storage)

    import app.services.knowledge_index_service as kis

    monkeypatch.setattr(kis, "get_storage", lambda: fake_storage)

    monkeypatch.setattr(knowledge_jobs, "tenant_knowledge_indexing_slot", _noop_slot)

    import app.services.audit_service as audit_mod

    monkeypatch.setattr(audit_mod, "log_action", _noop_audit)

    # Simular pypdf.PdfReader devolviendo páginas sin texto (PDF escaneado).
    mock_page = MagicMock()
    mock_page.extract_text.return_value = ""
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]

    doc = KnowledgeDocument(
        tenant_id=tenant.id,
        kind=KnowledgeDocumentKind.manual,
        name="Escaneo sin texto",
        original_filename="scan.pdf",
        source_file_key=f"tenants/{tenant.id}/docs/scan.pdf",
        source_mime="application/pdf",
        status=KnowledgeDocumentStatus.pending,
        chunk_count=0,
        file_size_bytes=len(_SCANNED_PDF_BYTES),
        uploaded_by=None,
    )
    db_session.add(doc)
    await db_session.commit()

    doc_id = doc.id
    tenant_id = tenant.id

    with patch("pypdf.PdfReader", return_value=mock_reader):
        result = await knowledge_jobs.index_knowledge_document({}, str(doc_id), str(tenant_id))

    assert result["status"] == "ok", f"Worker retornó: {result}"

    await set_tenant_context(db_session, str(tenant_id))
    db_session.expire_all()

    refreshed = await knowledge_document_service.get_document(
        db_session, tenant_id=tenant_id, document_id=doc_id, include_download_url=False
    )
    assert refreshed.status == KnowledgeDocumentStatus.failed
    assert refreshed.error_message is not None
    # El mensaje debe contener el prefijo de error conocido y la pista para el usuario.
    assert "empty_text" in refreshed.error_message
    assert "PDF" in refreshed.error_message or "escaneado" in refreshed.error_message

    reset_storage_for_tests()
