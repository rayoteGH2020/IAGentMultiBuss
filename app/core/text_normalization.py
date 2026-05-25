"""Normalización de texto para búsqueda tolerante a tildes y mayúsculas."""

from __future__ import annotations

import unicodedata


def normalize_search_text(value: str) -> str:
    """Quita acentos y pasa a minúsculas para comparación y patrones ILIKE.

    Equivalente conceptual a ``lower(unaccent(text))`` en Postgres. En SQL las
    queries usan la extensión ``unaccent``; este helper sirve para tests y
    patrones construidos en Python.
    """
    stripped = value.strip()
    if not stripped:
        return ""
    decomposed = unicodedata.normalize("NFD", stripped)
    without_marks = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return without_marks.lower()


def ilike_pattern(query: str) -> str:
    """Patrón seguro para ILIKE con comodines en extremos."""
    normalized = normalize_search_text(query)
    if not normalized:
        return "%"
    escaped = normalized.replace("%", r"\%").replace("_", r"\_")
    return f"%{escaped}%"
