"""Límites de recursos al inspeccionar PDFs e imágenes (hallazgo de seguridad #5)."""

from __future__ import annotations

import io

import pytest
from app.config import get_settings
from app.core.document_processing_errors import DocumentErrorCode
from app.core.media_limits import (
    MediaLimitExceeded,
    image_dimensions,
    inspect_document,
    open_image_within_limits,
    pdf_page_count,
)
from PIL import Image
from pypdf import PdfWriter


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


def _pdf_bytes(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _png_bytes(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_pdf_page_count_reads_real_pages() -> None:
    assert pdf_page_count(_pdf_bytes(3)) == 3


def test_pdf_within_limit_is_accepted() -> None:
    inspection = inspect_document(_pdf_bytes(3), "application/pdf")
    assert inspection.pages == 3
    assert inspection.mime_type == "application/pdf"


def test_pdf_over_limit_is_rejected_with_reason() -> None:
    with pytest.raises(MediaLimitExceeded) as exc_info:
        inspect_document(_pdf_bytes(4), "application/pdf")

    exc = exc_info.value
    assert exc.error_code is DocumentErrorCode.too_many_pages
    assert exc.detail is not None
    assert "4 páginas" in exc.detail
    assert exc.details["error_code"] == "too_many_pages"


def test_override_page_limit_allows_more_pages() -> None:
    inspection = inspect_document(_pdf_bytes(10), "application/pdf", max_pdf_pages=100)
    assert inspection.pages == 10


def test_corrupt_pdf_is_rejected_as_unreadable() -> None:
    with pytest.raises(MediaLimitExceeded) as exc_info:
        pdf_page_count(b"%PDF-1.4 esto no es un PDF valido")

    assert exc_info.value.error_code is DocumentErrorCode.unreadable_file


def test_image_dimensions_do_not_require_full_decode() -> None:
    assert image_dimensions(_png_bytes(120, 80)) == (120, 80)


def test_image_over_edge_limit_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCUMENT_MAX_IMAGE_EDGE_PX", "100")
    get_settings.cache_clear()

    with pytest.raises(MediaLimitExceeded) as exc_info:
        inspect_document(_png_bytes(200, 10), "image/png")

    assert exc_info.value.error_code is DocumentErrorCode.image_too_large


def test_image_over_pixel_area_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCUMENT_MAX_IMAGE_PIXELS", "1000")
    get_settings.cache_clear()

    with pytest.raises(MediaLimitExceeded) as exc_info:
        inspect_document(_png_bytes(100, 100), "image/png")

    assert exc_info.value.error_code is DocumentErrorCode.image_too_large


def test_open_image_within_limits_refuses_oversized(monkeypatch: pytest.MonkeyPatch) -> None:
    """La barrera se aplica también en la decodificación, no solo al inspeccionar."""
    monkeypatch.setenv("DOCUMENT_MAX_IMAGE_PIXELS", "1000")
    get_settings.cache_clear()

    with pytest.raises(MediaLimitExceeded):
        open_image_within_limits(_png_bytes(100, 100))


def test_open_image_within_limits_returns_rgb() -> None:
    image = open_image_within_limits(_png_bytes(40, 30))
    try:
        assert image.mode == "RGB"
        assert image.size == (40, 30)
    finally:
        image.close()


def test_unsupported_mime_is_rejected() -> None:
    with pytest.raises(MediaLimitExceeded) as exc_info:
        inspect_document(b"data", "application/octet-stream")

    assert exc_info.value.error_code is DocumentErrorCode.unsupported_type
