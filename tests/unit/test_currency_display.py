"""Tests del filtro de símbolo de moneda."""

from __future__ import annotations

from app.core.currency_display import currency_symbol


def test_currency_symbol_eur() -> None:
    assert currency_symbol("EUR") == "€"
    assert currency_symbol("eur") == "€"


def test_currency_symbol_unknown_falls_back_to_code() -> None:
    assert currency_symbol("SEK") == "SEK"


def test_currency_symbol_none_defaults_to_euro() -> None:
    assert currency_symbol(None) == "€"
    assert currency_symbol("  ") == "€"
