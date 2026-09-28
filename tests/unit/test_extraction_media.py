"""Tests de preprocesado de medios para extracción."""

from __future__ import annotations

import io

import pytest
from app.config import get_settings
from app.core.document_processing_errors import DocumentErrorCode
from app.core.media_limits import MediaLimitExceeded
from app.llm.extraction_media import prepare_classification_media, prepare_invoice_media
from PIL import Image
from pypdf import PdfReader, PdfWriter


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


def _make_pdf(page_count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _make_png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _make_jpeg(width: int, height: int, *, orientation: int | None = None) -> bytes:
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    buffer = io.BytesIO()
    if orientation is None:
        image.save(buffer, format="JPEG")
    else:
        exif = image.getexif()
        exif[0x0112] = orientation
        image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def test_prepare_invoice_media_passes_through_single_page_pdf() -> None:
    original = _make_pdf(1)
    result_bytes, result_mime, inspection = prepare_invoice_media(original, "application/pdf")
    assert result_bytes == original
    assert result_mime == "application/pdf"
    assert inspection.pages == 1


def test_prepare_invoice_media_sends_all_pages_up_to_the_limit() -> None:
    """Ya no se recorta a la primera página: o van las 3 o se rechaza el documento."""
    original = _make_pdf(3)
    result_bytes, result_mime, inspection = prepare_invoice_media(original, "application/pdf")
    assert result_mime == "application/pdf"
    assert result_bytes == original
    assert inspection.pages == 3
    assert len(PdfReader(io.BytesIO(result_bytes)).pages) == 3


def test_prepare_invoice_media_rejects_pdf_over_page_limit() -> None:
    with pytest.raises(MediaLimitExceeded) as exc_info:
        prepare_invoice_media(_make_pdf(4), "application/pdf")

    assert exc_info.value.error_code is DocumentErrorCode.too_many_pages


def test_prepare_invoice_media_accepts_more_pages_with_override() -> None:
    _, _, inspection = prepare_invoice_media(
        _make_pdf(8),
        "application/pdf",
        max_pdf_pages=100,
    )
    assert inspection.pages == 8


def test_prepare_invoice_media_downscales_large_png() -> None:
    original = _make_png(3000, 2000)

    result_bytes, result_mime, inspection = prepare_invoice_media(original, "image/png")
    assert result_mime == "image/jpeg"
    assert len(result_bytes) < len(original)
    assert inspection.pages == 1
    assert inspection.width == 3000
    with Image.open(io.BytesIO(result_bytes)) as optimized:
        assert max(optimized.size) <= 1280


def test_prepare_invoice_media_rejects_unreadable_image() -> None:
    with pytest.raises(MediaLimitExceeded) as exc_info:
        prepare_invoice_media(b"\xff\xd8\xff no soy un jpeg", "image/jpeg")

    assert exc_info.value.error_code is DocumentErrorCode.unreadable_file


def test_prepare_invoice_media_straightens_photo_taken_sideways() -> None:
    """La foto cabe en 1280 px, pero hay que recomprimirla igual para enderezarla."""
    original = _make_jpeg(40, 20, orientation=6)

    result_bytes, result_mime, _ = prepare_invoice_media(original, "image/jpeg")

    assert result_mime == "image/jpeg"
    with Image.open(io.BytesIO(result_bytes)) as straightened:
        assert straightened.size == (20, 40)
        assert straightened.getexif().get(0x0112) is None


def test_prepare_invoice_media_keeps_original_when_recompressing_does_not_help() -> None:
    """Un PNG plano y pequeño engorda al pasar a JPEG: se envía tal cual."""
    original = _make_png(40, 30)

    result_bytes, result_mime, _ = prepare_invoice_media(original, "image/png")

    assert result_bytes == original
    assert result_mime == "image/png"


def test_prepare_classification_media_downscales_images() -> None:
    original = _make_png(3000, 2000)

    result_bytes, result_mime = prepare_classification_media(original, "image/png")

    assert result_mime == "image/jpeg"
    assert len(result_bytes) < len(original)
    with Image.open(io.BytesIO(result_bytes)) as optimized:
        assert max(optimized.size) <= 1280


def test_prepare_classification_media_straightens_photo_taken_sideways() -> None:
    result_bytes, _ = prepare_classification_media(_make_jpeg(40, 20, orientation=6), "image/jpeg")

    with Image.open(io.BytesIO(result_bytes)) as straightened:
        assert straightened.size == (20, 40)


def test_prepare_classification_media_ignores_the_pdf_page_limit() -> None:
    """Clasificar no es extraer: el tope de páginas lo aplica el ingest, no esto."""
    original = _make_pdf(8)

    result_bytes, result_mime = prepare_classification_media(original, "application/pdf")

    assert result_bytes == original
    assert result_mime == "application/pdf"
