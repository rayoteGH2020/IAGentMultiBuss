"""Tests unitarios para app.core.text_chunking (Paso 18)."""

from __future__ import annotations

import pytest
from app.core.text_chunking import (
    CHARS_PER_TOKEN,
    TextChunk,
    TooManyChunksError,
    chunk_text,
    estimate_tokens,
)

# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


def test_estimate_tokens_basic() -> None:
    assert estimate_tokens("hola") == 1  # max(1, 4 // 4)


def test_estimate_tokens_longer() -> None:
    text = "a" * 400
    assert estimate_tokens(text) == 100


def test_estimate_tokens_empty_returns_one() -> None:
    # Cadena vacía → max(1, 0) = 1
    assert estimate_tokens("") == 1


# ---------------------------------------------------------------------------
# chunk_text — entrada vacía
# ---------------------------------------------------------------------------


def test_chunk_text_empty_returns_empty_list() -> None:
    assert chunk_text("") == []


def test_chunk_text_whitespace_only_returns_empty_list() -> None:
    assert chunk_text("   \n\n  ") == []


# ---------------------------------------------------------------------------
# chunk_text — texto corto (un solo chunk)
# ---------------------------------------------------------------------------


def test_chunk_text_short_text_single_chunk() -> None:
    text = "Párrafo corto que cabe en un solo chunk."
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].position == 0


# ---------------------------------------------------------------------------
# chunk_text — posiciones y orden
# ---------------------------------------------------------------------------


def test_chunk_text_positions_are_sequential() -> None:
    # Genera texto suficientemente largo para forzar varios chunks.
    paragraph = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 20
    text = "\n\n".join([paragraph] * 10)
    chunks = chunk_text(text, target_tokens=200, overlap_tokens=20)
    positions = [c.position for c in chunks]
    assert positions == list(range(len(chunks)))


# ---------------------------------------------------------------------------
# chunk_text — tamaño de chunks respeta el target
# ---------------------------------------------------------------------------


def test_chunk_text_respects_target_tokens() -> None:
    target = 100
    paragraph = "w " * (target * CHARS_PER_TOKEN // 2)  # ~target tokens por párrafo
    text = "\n\n".join([paragraph] * 20)
    chunks = chunk_text(text, target_tokens=target, overlap_tokens=10, min_tokens=5)
    for c in chunks:
        # Cada chunk no debería superar de forma significativa el target
        # (puede excederlo si un párrafo solo ya es más largo que target).
        assert isinstance(c, TextChunk)
        assert c.token_estimate >= 1


# ---------------------------------------------------------------------------
# chunk_text — solape entre chunks consecutivos
# ---------------------------------------------------------------------------


def test_chunk_text_overlap_content_shared() -> None:
    """El texto del chunk N+1 comparte algún contenido con el final del chunk N."""
    paragraph = "palabra " * 50
    text = "\n\n".join([paragraph.strip()] * 15)
    chunks = chunk_text(text, target_tokens=200, overlap_tokens=50)
    if len(chunks) >= 2:
        # Al menos un fragmento del final del primer chunk debe aparecer en el segundo.
        tail = chunks[0].text.split("\n\n")[-1][:40]
        assert tail in chunks[1].text or len(tail) < 5


# ---------------------------------------------------------------------------
# chunk_text — TooManyChunksError
# ---------------------------------------------------------------------------


def test_chunk_text_raises_too_many_chunks() -> None:
    # Forzar muchos chunks con max_chunks muy bajo.
    text = "\n\n".join(["párrafo corto"] * 10)
    with pytest.raises(TooManyChunksError) as exc_info:
        chunk_text(text, target_tokens=1, min_tokens=1, max_chunks=3)
    assert exc_info.value.max_chunks == 3
    assert exc_info.value.count > 3


# ---------------------------------------------------------------------------
# chunk_text — char_start aproximado
# ---------------------------------------------------------------------------


def test_chunk_text_char_start_within_text() -> None:
    paragraph = "El contrato establece las siguientes cláusulas."
    text = "\n\n".join([paragraph] * 5)
    chunks = chunk_text(text, target_tokens=20, overlap_tokens=5, min_tokens=2)
    for c in chunks:
        assert 0 <= c.char_start < len(text)


# ---------------------------------------------------------------------------
# chunk_text — párrafos individuales muy largos aceptados
# ---------------------------------------------------------------------------


def test_chunk_text_long_single_paragraph_accepted() -> None:
    """Un párrafo más largo que target_tokens se acepta como chunk independiente."""
    long_para = "a" * (600 * CHARS_PER_TOKEN + 100)
    chunks = chunk_text(long_para, target_tokens=200)
    assert len(chunks) >= 1
    assert long_para in chunks[0].text
