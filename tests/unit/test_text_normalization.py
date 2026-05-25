"""Tests de normalización de texto para búsqueda."""

from app.core.text_normalization import ilike_pattern, normalize_search_text


def test_normalize_search_text_strips_accents_and_lowercases() -> None:
    assert normalize_search_text("  Telefónica  ") == "telefonica"


def test_normalize_search_text_empty() -> None:
    assert normalize_search_text("") == ""
    assert normalize_search_text("   ") == ""


def test_ilike_pattern_wraps_normalized_query() -> None:
    assert ilike_pattern("Café") == "%cafe%"
