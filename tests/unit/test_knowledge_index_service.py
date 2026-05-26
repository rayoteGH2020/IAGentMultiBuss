"""Tests unitarios para knowledge_index_service.run_index_pipeline (Paso 18).

Usa mocks para R2, Voyage AI (LLMClient.embed) y la sesión de BD, de modo que
el pipeline completo pueda ejecutarse sin infraestructura real.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.core.text_chunking import TooManyChunksError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TENANT_ID = uuid4()
_DOC_ID = uuid4()
_FILE_KEY = f"tenants/{_TENANT_ID}/docs/test.txt"
_TXT_MIME = "text/plain"
_TEXT_BYTES = b"Contrato de servicios.\n\nCl\xc3\xa1usula 1: el proveedor se compromete."


def _make_db_mock() -> MagicMock:
    """Crea una sesión de BD async que acepta add_all, execute, flush y delete."""
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock())
    db.flush = AsyncMock()
    db.add_all = MagicMock()

    # _get_orm → devuelve un doc en estado pending
    from unittest.mock import AsyncMock as AM

    from app.models.knowledge import KnowledgeDocument, KnowledgeDocumentStatus

    doc = MagicMock(spec=KnowledgeDocument)
    doc.id = _DOC_ID
    doc.tenant_id = _TENANT_ID
    doc.status = KnowledgeDocumentStatus.pending
    doc.error_message = None
    doc.chunk_count = 0
    doc.ingested_at = None

    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = doc
    db.execute = AM(return_value=scalar_result)
    db.flush = AM()
    db.add_all = MagicMock()
    return db


# ---------------------------------------------------------------------------
# Pipeline completo → ready
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_index_pipeline_success() -> None:
    db = _make_db_mock()
    fake_embeddings = [[0.1] * 1536, [0.2] * 1536]

    with (
        patch("app.services.knowledge_index_service.get_storage") as mock_storage,
        patch("app.services.knowledge_index_service.get_llm_client") as mock_client,
        patch(
            "app.services.knowledge_index_service.mark_indexing", new_callable=AsyncMock
        ) as mock_mi,
        patch(
            "app.services.knowledge_index_service.apply_index_result", new_callable=AsyncMock
        ) as mock_air,
        patch(
            "app.services.knowledge_index_service.mark_failed", new_callable=AsyncMock
        ) as mock_mf,
        patch("app.services.audit_service.log_action", new_callable=AsyncMock),
    ):
        storage = AsyncMock()
        storage.download_bytes = AsyncMock(return_value=_TEXT_BYTES)
        mock_storage.return_value = storage

        llm = AsyncMock()
        llm.embed = AsyncMock(return_value=fake_embeddings[:1])  # 1 chunk
        mock_client.return_value = llm

        from app.services.knowledge_index_service import run_index_pipeline

        await run_index_pipeline(
            db,
            tenant_id=_TENANT_ID,
            document_id=_DOC_ID,
            source_file_key=_FILE_KEY,
            source_mime=_TXT_MIME,
        )

    mock_mi.assert_called_once()
    mock_air.assert_called_once()
    mock_mf.assert_not_called()


# ---------------------------------------------------------------------------
# Texto vacío (PDF escaneado) → mark_failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_index_pipeline_empty_text_marks_failed() -> None:
    db = _make_db_mock()
    empty_txt = b"   "  # texto vacío

    with (
        patch("app.services.knowledge_index_service.get_storage") as mock_storage,
        patch("app.services.knowledge_index_service.get_llm_client"),
        patch("app.services.knowledge_index_service.mark_indexing", new_callable=AsyncMock),
        patch(
            "app.services.knowledge_index_service.mark_failed", new_callable=AsyncMock
        ) as mock_mf,
        patch(
            "app.services.knowledge_index_service.apply_index_result", new_callable=AsyncMock
        ) as mock_air,
        patch("app.services.audit_service.log_action", new_callable=AsyncMock),
    ):
        storage = AsyncMock()
        storage.download_bytes = AsyncMock(return_value=empty_txt)
        mock_storage.return_value = storage

        from app.services.knowledge_index_service import run_index_pipeline

        await run_index_pipeline(
            db,
            tenant_id=_TENANT_ID,
            document_id=_DOC_ID,
            source_file_key=_FILE_KEY,
            source_mime=_TXT_MIME,
        )

    mock_mf.assert_called_once()
    args = mock_mf.call_args
    assert "empty_text" in args.kwargs.get("error_message", "")
    mock_air.assert_not_called()


# ---------------------------------------------------------------------------
# TooManyChunksError → mark_failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_index_pipeline_too_many_chunks_marks_failed() -> None:
    db = _make_db_mock()
    # Texto largo que superará max_chunks con target muy pequeño.
    long_text = ("párrafo de prueba.\n\n" * 600).encode()

    with (
        patch("app.services.knowledge_index_service.get_storage") as mock_storage,
        patch("app.services.knowledge_index_service.get_llm_client"),
        patch("app.services.knowledge_index_service.mark_indexing", new_callable=AsyncMock),
        patch(
            "app.services.knowledge_index_service.mark_failed", new_callable=AsyncMock
        ) as mock_mf,
        patch(
            "app.services.knowledge_index_service.apply_index_result", new_callable=AsyncMock
        ) as mock_air,
        patch("app.services.knowledge_index_service.chunk_text") as mock_chunk,
        patch("app.services.audit_service.log_action", new_callable=AsyncMock),
    ):
        storage = AsyncMock()
        storage.download_bytes = AsyncMock(return_value=long_text)
        mock_storage.return_value = storage

        mock_chunk.side_effect = TooManyChunksError(count=600, max_chunks=500)

        from app.services.knowledge_index_service import run_index_pipeline

        await run_index_pipeline(
            db,
            tenant_id=_TENANT_ID,
            document_id=_DOC_ID,
            source_file_key=_FILE_KEY,
            source_mime=_TXT_MIME,
        )

    mock_mf.assert_called_once()
    args = mock_mf.call_args
    assert "too_many_chunks" in args.kwargs.get("error_message", "")
    mock_air.assert_not_called()


# ---------------------------------------------------------------------------
# Error de embeddings → mark_failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_index_pipeline_embed_error_marks_failed() -> None:
    db = _make_db_mock()

    with (
        patch("app.services.knowledge_index_service.get_storage") as mock_storage,
        patch("app.services.knowledge_index_service.get_llm_client") as mock_client,
        patch("app.services.knowledge_index_service.mark_indexing", new_callable=AsyncMock),
        patch(
            "app.services.knowledge_index_service.mark_failed", new_callable=AsyncMock
        ) as mock_mf,
        patch(
            "app.services.knowledge_index_service.apply_index_result", new_callable=AsyncMock
        ) as mock_air,
        patch("app.services.audit_service.log_action", new_callable=AsyncMock),
    ):
        storage = AsyncMock()
        storage.download_bytes = AsyncMock(return_value=_TEXT_BYTES)
        mock_storage.return_value = storage

        llm = AsyncMock()
        llm.embed = AsyncMock(side_effect=RuntimeError("Voyage AI timeout"))
        mock_client.return_value = llm

        from app.services.knowledge_index_service import run_index_pipeline

        await run_index_pipeline(
            db,
            tenant_id=_TENANT_ID,
            document_id=_DOC_ID,
            source_file_key=_FILE_KEY,
            source_mime=_TXT_MIME,
        )

    mock_mf.assert_called_once()
    args = mock_mf.call_args
    assert "embed_error" in args.kwargs.get("error_message", "")
    mock_air.assert_not_called()
