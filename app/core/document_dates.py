"""Fecha de emisión leída del texto de un PDF, sin LLM (D017, histórico del plan).

Solo se acepta una fecha con su etiqueta de emisión («Fecha», «Fecha de factura»,
«Fecha de emisión», «Fecha de expedición»): una fecha suelta puede ser la de
vencimiento, pago o servicio. Si aparecen varias fechas etiquetadas distintas, no
se decide (``None``) y manda la extracción completa.
"""

from __future__ import annotations

import re
from datetime import date

_MONTHS_ES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

_NUMERIC = r"(?P<d>\d{1,2})[/\-.](?P<m>\d{1,2})[/\-.](?P<y>\d{4}|\d{2})"
_ISO = r"(?P<iy>\d{4})-(?P<im>\d{2})-(?P<id>\d{2})"
_WORDS = r"(?P<wd>\d{1,2})\s+de\s+(?P<wm>[a-záéíóú]+)\s+(?:de\s+|del\s+)?(?P<wy>\d{4})"

# «Fecha», «Fecha de factura», «Fecha emisión»... seguida de la fecha. «Fecha de
# vencimiento» no casa: tras «de» tendría que venir una de las etiquetas o la fecha.
_LABELLED_DATE = re.compile(
    r"fecha(?:\s+de)?(?:\s+(?:la\s+)?(?:factura|emisi[oó]n|expedici[oó]n))?\s*[:\-]?\s*"
    rf"(?:{_ISO}|{_NUMERIC}|{_WORDS})",
    re.IGNORECASE,
)


def _build(year: int, month: int, day: int) -> date | None:
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse(match: re.Match[str]) -> date | None:
    if match.group("iy"):
        return _build(int(match.group("iy")), int(match.group("im")), int(match.group("id")))
    if match.group("d"):
        return _build(int(match.group("y")), int(match.group("m")), int(match.group("d")))
    month = _MONTHS_ES.get(match.group("wm").lower())
    if month is None:
        return None
    return _build(int(match.group("wy")), month, int(match.group("wd")))


def find_issue_date(text: str) -> date | None:
    """Fecha de emisión etiquetada del texto; ``None`` si no hay o es ambigua."""
    if not text:
        return None
    found = {parsed for m in _LABELLED_DATE.finditer(text) if (parsed := _parse(m)) is not None}
    return found.pop() if len(found) == 1 else None
