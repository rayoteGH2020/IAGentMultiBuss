"""Símbolos de moneda para plantillas (ISO 4217 → glifo)."""

from __future__ import annotations

_CURRENCY_SYMBOLS: dict[str, str] = {
    "EUR": "€",
    "USD": "$",
    "GBP": "£",
    "CHF": "CHF",
    "JPY": "¥",
    "CNY": "¥",
    "MXN": "$",
    "ARS": "$",
    "BRL": "R$",
    "CAD": "C$",
    "AUD": "A$",
}


def currency_symbol(code: str | None) -> str:
    """Devuelve el símbolo de la moneda; si no hay mapeo, el código ISO."""
    if code is None:
        return "€"
    normalized = code.strip().upper()
    if not normalized:
        return "€"
    return _CURRENCY_SYMBOLS.get(normalized, normalized)
