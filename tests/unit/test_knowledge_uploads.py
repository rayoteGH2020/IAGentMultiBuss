"""Tests unitarios para app.core.knowledge_uploads (Paso 18)."""

from __future__ import annotations

import pytest
from app.core.knowledge_uploads import validate_knowledge_upload
from app.core.uploads import UploadValidationError

_ALLOWED = ["application/pdf", "text/plain", "text/markdown", "text/x-markdown"]
_MAX = 15 * 1024 * 1024  # 15 MB

# Cabecera mínima de PDF válida para superar la detección por firma.
_PDF_HEADER = b"%PDF-1.4\n%content\n"
_TXT_CONTENT = b"Hola mundo. Este es un documento de texto plano."
_MD_CONTENT = b"# FAQ\n\n## Pregunta 1\nRespuesta breve."


# ---------------------------------------------------------------------------
# PDF por firma binaria
# ---------------------------------------------------------------------------


def test_pdf_signature_detected() -> None:
    mime = validate_knowledge_upload("doc.pdf", _PDF_HEADER + b"\x00" * 10, _MAX, _ALLOWED)
    assert mime == "application/pdf"


# ---------------------------------------------------------------------------
# Texto plano UTF-8
# ---------------------------------------------------------------------------


def test_txt_content_detected() -> None:
    mime = validate_knowledge_upload("readme.txt", _TXT_CONTENT, _MAX, _ALLOWED)
    assert mime == "text/plain"


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def test_markdown_content_detected() -> None:
    # Markdown no tiene firma binaria; se detecta como text/plain vía fallback.
    mime = validate_knowledge_upload("faq.md", _MD_CONTENT, _MAX, _ALLOWED)
    assert mime in {"text/plain", "text/markdown", "text/x-markdown"}


# ---------------------------------------------------------------------------
# Fichero vacío → error
# ---------------------------------------------------------------------------


def test_empty_file_raises_error() -> None:
    with pytest.raises(UploadValidationError, match=r"[Ee]mpty"):
        validate_knowledge_upload("empty.txt", b"", _MAX, _ALLOWED)


# ---------------------------------------------------------------------------
# Fichero demasiado grande → error
# ---------------------------------------------------------------------------


def test_oversized_file_raises_error() -> None:
    big = b"a" * (_MAX + 1)
    with pytest.raises(UploadValidationError, match=r"[Tt]oo large|[Ss]ize"):
        validate_knowledge_upload("big.txt", big, _MAX, _ALLOWED)


# ---------------------------------------------------------------------------
# Tipo no soportado → error
# ---------------------------------------------------------------------------


def test_unsupported_binary_raises_error() -> None:
    # ZIP magic bytes: PK\x03\x04
    zip_bytes = b"PK\x03\x04" + b"\x00" * 100
    with pytest.raises(UploadValidationError, match=r"[Uu]nsupported"):
        validate_knowledge_upload("archive.zip", zip_bytes, _MAX, _ALLOWED)


def test_image_jpeg_raises_error() -> None:
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    with pytest.raises(UploadValidationError, match=r"[Uu]nsupported"):
        validate_knowledge_upload("photo.jpg", jpeg_bytes, _MAX, _ALLOWED)


# ---------------------------------------------------------------------------
# Restricción de MIMEs personalizados
# ---------------------------------------------------------------------------


def test_text_rejected_when_not_in_allowed_list() -> None:
    allowed_pdf_only = ["application/pdf"]
    with pytest.raises(UploadValidationError, match=r"[Uu]nsupported"):
        validate_knowledge_upload("readme.txt", _TXT_CONTENT, _MAX, allowed_pdf_only)
