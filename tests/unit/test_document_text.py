"""Tests unitarios para app.core.document_text (Paso 18)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from app.config import get_settings
from app.core.document_text import (
    ExtractedTextResult,
    extract_document_text,
    extract_knowledge_text,
)

_TXT_BYTES = b"Hola mundo. Este es texto plano en UTF-8."
# "Sección" y "sección" en UTF-8 (ó = \xc3\xb3)
_MD_BYTES = b"# Titulo\n\n## Secci\xc3\xb3n 1\nContenido de la secci\xc3\xb3n."


# ---------------------------------------------------------------------------
# Texto plano (text/plain)
# ---------------------------------------------------------------------------


def test_extract_plain_text_returns_utf8_string() -> None:
    result = extract_knowledge_text(_TXT_BYTES, "text/plain")
    assert isinstance(result, ExtractedTextResult)
    assert result.text == _TXT_BYTES.decode("utf-8")
    assert result.char_count == len(result.text)
    assert result.page_count is None
    assert result.warnings == []


def test_extract_plain_text_no_warnings() -> None:
    result = extract_knowledge_text(_TXT_BYTES, "text/plain")
    assert result.warnings == []


# ---------------------------------------------------------------------------
# Markdown (text/markdown y text/x-markdown)
# ---------------------------------------------------------------------------


def test_extract_markdown_text_markdown_mime() -> None:
    result = extract_knowledge_text(_MD_BYTES, "text/markdown")
    assert "Titulo" in result.text
    assert result.page_count is None


def test_extract_markdown_x_markdown_mime() -> None:
    result = extract_knowledge_text(_MD_BYTES, "text/x-markdown")
    assert "Secci" in result.text


# ---------------------------------------------------------------------------
# UTF-8 con caracteres inválidos → replace en lugar de crash
# ---------------------------------------------------------------------------


def test_extract_plain_text_invalid_utf8_replaced() -> None:
    bad_bytes = b"Texto \xff\xfe con bytes inv\xc3\xa1lidos"
    result = extract_knowledge_text(bad_bytes, "text/plain")
    assert isinstance(result.text, str)
    assert "�" in result.text  # U+FFFD: replacement character


# ---------------------------------------------------------------------------
# PDF con texto (PDF real mínimo simulado con mock)
# ---------------------------------------------------------------------------


def test_extract_pdf_with_text() -> None:
    """PDF digital con capa de texto: devuelve texto y page_count correcto."""
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Contenido del PDF página 1"
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page, mock_page]

    # PdfReader se importa lazy dentro de _extract_pdf; se parchea en pypdf.
    with patch("pypdf.PdfReader", return_value=mock_reader):
        result = extract_knowledge_text(b"%PDF-1.4 fake", "application/pdf")

    assert result.text.count("Contenido") == 2
    assert result.page_count == 2
    assert "scanned_pdf_suspected" not in result.warnings


# ---------------------------------------------------------------------------
# PDF escaneado (sin texto): retorna texto vacío + warning
# ---------------------------------------------------------------------------


def test_extract_scanned_pdf_returns_empty_with_warning() -> None:
    """PDF escaneado: pypdf no puede extraer texto → empty_text → warning."""
    mock_page = MagicMock()
    mock_page.extract_text.return_value = ""  # sin capa de texto
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]

    with patch("pypdf.PdfReader", return_value=mock_reader):
        result = extract_knowledge_text(b"%PDF-1.4 scanned", "application/pdf")

    assert result.text.strip() == ""
    assert "scanned_pdf_suspected" in result.warnings


# ---------------------------------------------------------------------------
# PDF con error de parseo: retorna ExtractedTextResult vacío con warning
# ---------------------------------------------------------------------------


def test_extract_pdf_parse_error_returns_empty() -> None:
    with patch("pypdf.PdfReader", side_effect=Exception("bad pdf")):
        result = extract_knowledge_text(b"not-a-pdf", "application/pdf")

    assert result.text == ""
    assert any("pdf_parse_error" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# MIME no soportado: devuelve vacío con warning unsupported_mime
# ---------------------------------------------------------------------------


def test_extract_unsupported_mime_returns_empty() -> None:
    result = extract_knowledge_text(b"data", "application/octet-stream")
    assert result.text == ""
    assert "unsupported_mime" in result.warnings


# ---------------------------------------------------------------------------
# Límites de recursos (hallazgo de seguridad #5)
# ---------------------------------------------------------------------------


def test_knowledge_pdf_over_page_limit_is_rejected_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mejor rechazar el libro entero que indexar un trozo silenciosamente."""
    monkeypatch.setenv("KNOWLEDGE_MAX_PDF_PAGES", "2")
    get_settings.cache_clear()

    mock_page = MagicMock()
    mock_page.extract_text.return_value = "texto"
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page] * 5

    try:
        with patch("pypdf.PdfReader", return_value=mock_reader):
            result = extract_knowledge_text(b"%PDF-1.4 fake", "application/pdf")
    finally:
        get_settings.cache_clear()

    assert result.text == ""
    assert result.page_count == 5
    assert "too_many_pages:5" in result.warnings


def test_knowledge_pdf_text_is_truncated_at_char_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_MAX_EXTRACTED_CHARS", "50")
    get_settings.cache_clear()

    mock_page = MagicMock()
    mock_page.extract_text.return_value = "x" * 40
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page] * 10

    try:
        with patch("pypdf.PdfReader", return_value=mock_reader):
            result = extract_knowledge_text(b"%PDF-1.4 fake", "application/pdf")
    finally:
        get_settings.cache_clear()

    assert result.char_count == 50
    assert "text_truncated:50" in result.warnings


def test_classification_text_stops_at_document_page_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La extracción para clasificar no recorre un PDF entero de miles de páginas."""
    monkeypatch.setenv("DOCUMENT_MAX_PDF_PAGES", "3")
    get_settings.cache_clear()

    mock_page = MagicMock()
    mock_page.extract_text.return_value = "pagina"
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page] * 50

    try:
        with patch("pypdf.PdfReader", return_value=mock_reader):
            text = extract_document_text(b"%PDF-1.4 fake", "application/pdf")
    finally:
        get_settings.cache_clear()

    assert text.count("pagina") == 3
