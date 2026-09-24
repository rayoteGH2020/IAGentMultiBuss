"""OCR knowledge: toda imagen pasa por media_limits antes del LLM (Paso01 §5)."""

from __future__ import annotations

import io
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.config import get_settings
from app.core.document_processing_errors import DocumentErrorCode
from app.core.media_limits import MediaLimitExceeded
from app.core.uploads import UploadValidationError
from app.schemas.entitlements import Entitlements
from app.schemas.knowledge import KnowledgeDocumentKind
from PIL import Image

_TENANT_ID = uuid4()
_DOC_ID = uuid4()
_USER_ID = uuid4()
_FILE_KEY = f"tenants/{_TENANT_ID}/docs/bomb.png"


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-key-for-unit-tests")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://saas:saas@localhost:5432/saas",  # pragma: allowlist secret
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _png_bytes(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _make_db_mock() -> MagicMock:
    db = MagicMock()
    from app.models.knowledge import KnowledgeDocument

    doc = MagicMock(spec=KnowledgeDocument)
    doc.id = _DOC_ID
    doc.tenant_id = _TENANT_ID
    doc.error_message = None
    doc.chunk_count = 0
    doc.ingested_at = None

    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = doc
    db.execute = AsyncMock(return_value=scalar_result)
    db.flush = AsyncMock()
    db.add_all = MagicMock()
    db.add = MagicMock()
    return db


# ---------------------------------------------------------------------------
# extract_text_from_image: inspecciona antes del LLM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_text_from_image_rejects_oversized_before_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCUMENT_MAX_IMAGE_EDGE_PX", "100")
    get_settings.cache_clear()

    mock_client = AsyncMock()
    mock_client.complete = AsyncMock()

    with patch("app.llm.extraction.get_llm_client", return_value=mock_client):
        from app.llm.extraction import extract_text_from_image

        with pytest.raises(MediaLimitExceeded) as exc_info:
            await extract_text_from_image(
                file_bytes=_png_bytes(200, 10),
                mime_type="image/png",
                tenant_id=_TENANT_ID,
                db=MagicMock(),
            )

    assert exc_info.value.error_code is DocumentErrorCode.image_too_large
    mock_client.complete.assert_not_called()


@pytest.mark.asyncio
async def test_extract_text_from_image_rejects_unreadable_before_llm() -> None:
    mock_client = AsyncMock()
    mock_client.complete = AsyncMock()

    with patch("app.llm.extraction.get_llm_client", return_value=mock_client):
        from app.llm.extraction import extract_text_from_image

        with pytest.raises(MediaLimitExceeded) as exc_info:
            await extract_text_from_image(
                file_bytes=b"not-an-image",
                mime_type="image/png",
                tenant_id=_TENANT_ID,
                db=MagicMock(),
            )

    assert exc_info.value.error_code is DocumentErrorCode.unreadable_file
    mock_client.complete.assert_not_called()


@pytest.mark.asyncio
async def test_extract_text_from_image_accepts_within_limits() -> None:
    from app.llm.client import LLMCompleteResult
    from app.llm.extraction import _ImageOCRResult

    mock_client = AsyncMock()
    mock_client.complete = AsyncMock(
        return_value=LLMCompleteResult(
            result=_ImageOCRResult(text="hola knowledge"),
            llm_call_id=uuid4(),
        )
    )

    with patch("app.llm.extraction.get_llm_client", return_value=mock_client):
        from app.llm.extraction import extract_text_from_image

        text = await extract_text_from_image(
            file_bytes=_png_bytes(40, 30),
            mime_type="image/png",
            tenant_id=_TENANT_ID,
            db=MagicMock(),
        )

    assert text == "hola knowledge"
    mock_client.complete.assert_awaited_once()


# ---------------------------------------------------------------------------
# Pipeline de indexación: media_limit sin OCR / sin filtrar paths internos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_index_pipeline_image_over_limits_marks_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCUMENT_MAX_IMAGE_PIXELS", "1000")
    get_settings.cache_clear()

    db = _make_db_mock()
    bomb = _png_bytes(100, 100)

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
        storage.download_bytes = AsyncMock(return_value=bomb)
        mock_storage.return_value = storage
        llm = AsyncMock()
        llm.complete = AsyncMock()
        mock_client.return_value = llm

        from app.services.knowledge_index_service import run_index_pipeline

        await run_index_pipeline(
            db,
            tenant_id=_TENANT_ID,
            document_id=_DOC_ID,
            source_file_key=_FILE_KEY,
            source_mime="image/png",
        )

    mock_mf.assert_called_once()
    message = mock_mf.call_args.kwargs.get("error_message", "")
    assert "media_limit" in message
    assert "C:\\" not in message
    mock_air.assert_not_called()
    llm.complete.assert_not_called()


# ---------------------------------------------------------------------------
# Subida knowledge: rechaza antes de R2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_from_upload_rejects_oversized_image_before_r2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCUMENT_MAX_IMAGE_EDGE_PX", "50")
    get_settings.cache_clear()

    db = _make_db_mock()
    bomb = _png_bytes(200, 10)

    with patch("app.services.knowledge_document_service.get_storage") as mock_storage:
        storage = AsyncMock()
        storage.upload_bytes = AsyncMock()
        mock_storage.return_value = storage

        from app.services.knowledge_document_service import create_from_upload

        with (
            pytest.raises(UploadValidationError) as exc_info,
            patch(
                "app.services.entitlement_service.resolve_tenant",
                AsyncMock(
                    return_value=Entitlements(
                        plan_code="basic",
                        features=frozenset({"knowledge"}),
                        limits={"knowledge_docs_max": Decimal("50")},
                        fail_closed=False,
                    )
                ),
            ),
            patch(
                "app.services.plan_quota_service.ensure_knowledge_docs_capacity",
                AsyncMock(),
            ),
        ):
            await create_from_upload(
                db,
                tenant_id=_TENANT_ID,
                user_id=_USER_ID,
                filename="huge.png",
                file_bytes=bomb,
                kind=KnowledgeDocumentKind.policy,
            )

    assert "px" in str(exc_info.value).lower() or "límite" in str(exc_info.value).lower()
    storage.upload_bytes.assert_not_called()
    db.add.assert_not_called()
