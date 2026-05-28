"""Mensajes de error de procesamiento documental legibles para el usuario."""

from __future__ import annotations

import re

# Etiquetas amigables por campo del schema de extracción.
_FIELD_REASONS: dict[str, str] = {
    "cif_nif": "No se ha podido validar el CIF/NIF presente en el documento.",
    "proveedor": "No se ha podido identificar el proveedor o emisor del documento.",
    "comercio": "No se ha podido identificar el comercio del ticket.",
    "fecha": "No se ha podido leer la fecha del documento.",
    "total": "No se ha podido validar el importe total.",
    "base_imponible": "No se ha podido validar la base imponible.",
    "iva_percent": "No se ha podido validar el porcentaje de IVA.",
    "iva_amount": "No se ha podido validar el importe de IVA.",
    "lineas": "No se han podido validar las líneas de detalle.",
    "numero_factura": "No se ha podido leer el número de factura.",
    "numero_ticket": "No se ha podido leer el número de ticket.",
    "forma_pago": "No se ha podido leer la forma de pago.",
    "currency": "No se ha podido validar la moneda del documento.",
    "confidence": "No se ha podido completar la extracción con suficiente confianza.",
}

_KNOWN_LITERALS: tuple[tuple[str, str], ...] = (
    ("missing source_file_key", "No encontramos el archivo subido. Vuelve a subirlo."),
    ("file too large", "El archivo supera el tamaño máximo permitido (20 MB)."),
    ("unsupported mime type", "El formato del archivo no es compatible."),
    ("rate limit", "El servicio está ocupado. Inténtalo de nuevo en unos minutos."),
    ("timeout", "El procesamiento tardó demasiado. Inténtalo de nuevo."),
    ("connection", "No se pudo conectar con el servicio de extracción. Inténtalo más tarde."),
)

_VALIDATION_FIELD_RE = re.compile(
    r"validation error for (?:Factura|TicketRecibo)\s+(\w+)",
    re.IGNORECASE,
)
_EXCEPTION_BLOCK_RE = re.compile(r"<exception>\s*(.*?)\s*</exception>", re.IGNORECASE | re.DOTALL)
_FIELD_TOKEN_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _FIELD_REASONS) + r")\b",
    re.IGNORECASE,
)


def _normalize_raw_error(raw: str) -> str:
    text = raw.strip()
    if not text:
        return text
    match = _EXCEPTION_BLOCK_RE.search(text)
    if match:
        return " ".join(match.group(1).split())
    # Respuestas Instructor/Gemini con XML: quedarse con el trozo útil.
    if "<failed_attempts>" in text.lower() or "<generation" in text.lower():
        for marker in ("validation error for", "validation error"):
            idx = text.lower().find(marker)
            if idx >= 0:
                return " ".join(text[idx : idx + 400].split())
    return " ".join(text.split())


def _reason_from_error(normalized: str) -> str:
    lower = normalized.lower()

    for needle, message in _KNOWN_LITERALS:
        if needle in lower:
            return message

    field_match = _VALIDATION_FIELD_RE.search(normalized)
    if field_match:
        field = field_match.group(1).lower()
        if field in _FIELD_REASONS:
            return _FIELD_REASONS[field]

    token_match = _FIELD_TOKEN_RE.search(normalized)
    if token_match:
        field = token_match.group(1).lower()
        if field in _FIELD_REASONS:
            return _FIELD_REASONS[field]

    if "string_pattern_mismatch" in lower and "cif" in lower:
        return _FIELD_REASONS["cif_nif"]

    if "string_pattern_mismatch" in lower:
        return "Algunos datos extraídos no tienen un formato válido."

    if "input_value='null'" in lower or 'input_value="null"' in lower:
        return "Faltan datos obligatorios que no se pudieron leer en el documento."

    if "validation error" in lower:
        return "No se pudieron validar los datos extraídos del documento."

    if "llm call failed" in lower or "complete()" in lower:
        return "No se pudo completar la lectura automática del documento."

    return "No se pudieron extraer los datos del documento. Comprueba que el archivo sea legible."


def format_user_processing_error(
    raw_error: str | None,
    *,
    filename: str | None,
) -> str:
    """Convierte un error técnico en mensaje para mostrar en la UI."""
    display_name = (filename or "").strip() or "documento"
    if not raw_error or not raw_error.strip():
        return (
            f'Error al procesar el documento "{display_name}". No se pudo completar la extracción.'
        )

    if raw_error.startswith('Error al procesar el documento "'):
        return raw_error

    normalized = _normalize_raw_error(raw_error)
    reason = _reason_from_error(normalized)
    return f'Error al procesar el documento "{display_name}". {reason}'
