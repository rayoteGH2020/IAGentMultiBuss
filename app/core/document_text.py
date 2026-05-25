"""Extracción de texto plano de documentos para clasificación heurística."""

from __future__ import annotations

import io


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
