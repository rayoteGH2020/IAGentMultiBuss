"""Chunking de texto para indexación RAG (módulo 2, Paso 18).

Estrategia: split por párrafos con solape configurable. Se preservan los
límites de párrafo para mantener coherencia semántica entre chunks vecinos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ~4 chars/token es la estimación estándar para texto en español con tokenización BPE.
# Más preciso que dividir por espacios (subestima) y más barato que cargar un
# tokenizador real en cada llamada (sobrecarga innecesaria para MVP).
CHARS_PER_TOKEN: int = 4


@dataclass
class TextChunk:
    """Fragmento de texto listo para embeber."""

    text: str
    # Índice de orden dentro del documento (0-based); se persiste en knowledge_chunks.position.
    position: int
    # Estimación de tokens del chunk; se guarda en metadata JSONB para diagnóstico.
    token_estimate: int
    # Posición aproximada en el texto original; útil para debug y evals, no crítica para RAG.
    char_start: int
    # Solo disponible si el extractor de texto reporta páginas (PDFs con page boundaries);
    # siempre None en el pipeline MVP actual.
    page_no: int | None = None
    # Metadatos adicionales para extensiones futuras (contextual retrieval, etc.).
    extra: dict[str, object] = field(default_factory=dict)


class TooManyChunksError(Exception):
    """El documento supera el límite de chunks, probablemente por ser demasiado largo."""

    def __init__(self, count: int, max_chunks: int) -> None:
        super().__init__(f"too_many_chunks: {count} > {max_chunks}")
        self.count = count
        self.max_chunks = max_chunks


def estimate_tokens(text: str) -> int:
    """Estimación rápida de tokens por longitud de caracteres."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def _split_into_paragraphs(text: str) -> list[str]:
    """Divide el texto en unidades semánticas (párrafos).

    Orden de preferencia:
    1. Doble salto de línea (estándar Markdown y prosa).
    2. Salto de línea simple (texto plano sin formato).
    3. Texto completo como única unidad (fallback).
    """
    parts = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    if len(parts) > 1:
        return parts
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    if parts:
        return parts
    stripped = text.strip()
    return [stripped] if stripped else []


def _build_raw_chunks(
    paragraphs: list[str],
    target_chars: int,
    overlap_chars: int,
) -> list[str]:
    """Construye los chunks crudos con solape por cola de párrafos."""
    raw: list[str] = []
    buf: list[str] = []
    buf_chars: int = 0

    for para in paragraphs:
        para_chars = len(para)

        # Vaciar buffer cuando agregar este párrafo superaría el objetivo.
        # La guarda `and buf` garantiza que párrafos más largos que target_chars
        # se aceptan solos (no se descartan ni dividen).
        if buf_chars + para_chars > target_chars and buf:
            raw.append("\n\n".join(buf))

            # Construir solape: tomar los últimos K párrafos del buffer actual
            # que quepan dentro de overlap_chars.
            overlap: list[str] = []
            overlap_total: int = 0
            for p in reversed(buf):
                # +2 por el separador '\n\n' entre párrafos (excepto el primero).
                separator = 2 if overlap else 0
                if overlap_total + len(p) + separator <= overlap_chars:
                    overlap.insert(0, p)
                    overlap_total += len(p) + separator
                else:
                    break

            buf = overlap
            buf_chars = sum(len(p) for p in buf)

        buf.append(para)
        buf_chars += para_chars

    if buf:
        raw.append("\n\n".join(buf))

    return raw


def _merge_short_chunks(chunks: list[str], min_chars: int) -> list[str]:
    """Fusiona chunks demasiado cortos con el anterior para evitar embeddings vacíos."""
    merged: list[str] = []
    for chunk in chunks:
        if len(chunk) < min_chars and merged:
            merged[-1] = merged[-1] + "\n\n" + chunk
        else:
            merged.append(chunk)
    return merged


def _find_char_start(text: str, chunk_str: str) -> int:
    """Localiza la posición aproximada del chunk en el texto original."""
    anchor = chunk_str[:100]
    idx = text.find(anchor)
    return max(0, idx)


def chunk_text(
    text: str,
    *,
    target_tokens: int = 600,
    overlap_tokens: int = 100,
    min_tokens: int = 80,
    max_chunks: int = 500,
) -> list[TextChunk]:
    """Divide ``text`` en chunks con solape para indexación RAG.

    Args:
        text: Texto completo extraído del documento.
        target_tokens: Tamaño objetivo por chunk en tokens estimados.
        overlap_tokens: Tokens de solape entre chunks consecutivos.
        min_tokens: Tamaño mínimo; chunks menores se fusionan con el anterior.
        max_chunks: Máximo de chunks por documento; superar este límite indica
            un documento inusualmente largo y protege el coste de embeddings.

    Returns:
        Lista ordenada de ``TextChunk``. Vacía si el texto está vacío.

    Raises:
        TooManyChunksError: si el número de chunks resultante supera ``max_chunks``.
    """
    if not text.strip():
        return []

    target_chars = target_tokens * CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * CHARS_PER_TOKEN
    min_chars = min_tokens * CHARS_PER_TOKEN

    paragraphs = _split_into_paragraphs(text)
    raw = _build_raw_chunks(paragraphs, target_chars, overlap_chars)
    merged = _merge_short_chunks(raw, min_chars)

    if len(merged) > max_chunks:
        raise TooManyChunksError(len(merged), max_chunks)

    return [
        TextChunk(
            text=chunk_str,
            position=i,
            token_estimate=estimate_tokens(chunk_str),
            char_start=_find_char_start(text, chunk_str),
            page_no=None,
        )
        for i, chunk_str in enumerate(merged)
    ]
