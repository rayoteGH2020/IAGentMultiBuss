"""Extracción de texto plano de documentos.

Dos funciones públicas con responsabilidades distintas:
- ``extract_document_text``: extracción ligera para clasificación heurística
  (módulo 1). Solo PDF; sin metadatos adicionales.
- ``extract_knowledge_text``: extracción completa para indexación RAG (módulo 2).
  Soporta PDF, texto plano y Markdown; devuelve ``ExtractedTextResult`` con
  métricas y warnings accionables por el worker ARQ.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field


@dataclass
class ExtractedTextResult:
    """Resultado de la extracción de texto para el pipeline RAG."""

    text: str
    char_count: int
    # page_count es None para ficheros de texto (no hay concepto de páginas).
    page_count: int | None
    warnings: list[str] = field(default_factory=list)


def extract_document_text(file_bytes: bytes, mime_type: str) -> str:
    """Devuelve texto extraíble del documento (PDF con capa de texto).

    Imágenes y PDFs escaneados devuelven cadena vacía; la clasificación
    automática usará el fallback LLM en esos casos.
    """
    if mime_type != "application/pdf":
        return ""

    try:
        from pypdf import PdfReader
    except ImportError:
        return ""

    try:
        reader = PdfReader(io.BytesIO(file_bytes))
    except Exception:
        return ""

    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        if text.strip():
            parts.append(text)
    return "\n".join(parts)


def extract_knowledge_text(file_bytes: bytes, mime_type: str) -> ExtractedTextResult:
    """Extrae texto plano de un documento para indexación en RAG.

    Soporta PDF digital, ``text/plain``, ``text/markdown`` y ``text/x-markdown``.
    Los PDFs escaneados (sin capa de texto) devuelven texto vacío con el warning
    ``scanned_pdf_suspected`` para que el worker ARQ los marque como ``failed``.

    Args:
        file_bytes: Bytes completos del fichero.
        mime_type: MIME ya validado por ``validate_knowledge_upload``.

    Returns:
        ``ExtractedTextResult`` con el texto, métricas y lista de warnings.
    """
    if mime_type == "application/pdf":
        return _extract_pdf(file_bytes)
    if mime_type in ("text/plain", "text/markdown", "text/x-markdown"):
        return _extract_plain_text(file_bytes)
    # Tipo no soportado: no debería llegar aquí tras validate_knowledge_upload.
    return ExtractedTextResult(
        text="",
        char_count=0,
        page_count=None,
        warnings=["unsupported_mime"],
    )


def _extract_pdf(file_bytes: bytes) -> ExtractedTextResult:
    """Extrae texto de un PDF con capa de texto usando pypdf."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ExtractedTextResult(
            text="", char_count=0, page_count=None, warnings=["pypdf_not_available"]
        )

    try:
        reader = PdfReader(io.BytesIO(file_bytes))
    except Exception as exc:
        return ExtractedTextResult(
            text="",
            char_count=0,
            page_count=None,
            warnings=[f"pdf_parse_error:{exc!s:.200}"],
        )

    page_count = len(reader.pages)
    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        if text.strip():
            parts.append(text)

    full_text = "\n".join(parts)
    warnings: list[str] = []
    # PDF escaneado: pypdf no puede extraer texto de imágenes embebidas.
    # El worker debe rechazar el documento con un mensaje claro al usuario.
    if not full_text.strip():
        warnings.append("scanned_pdf_suspected")

    return ExtractedTextResult(
        text=full_text,
        char_count=len(full_text),
        page_count=page_count,
        warnings=warnings,
    )


def _extract_plain_text(file_bytes: bytes) -> ExtractedTextResult:
    """Decodifica texto plano o Markdown como UTF-8."""
    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        # errors="replace" sustituye bytes inválidos por U+FFFD en lugar de
        # abortar; preferible a perder el documento completo por una codificación
        # mixta (p. ej. Latin-1 embebido en un fichero mayoritariamente UTF-8).
        text = file_bytes.decode("utf-8", errors="replace")

    return ExtractedTextResult(
        text=text,
        char_count=len(text),
        page_count=None,
        warnings=[],
    )
