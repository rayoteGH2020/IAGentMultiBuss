"""Clasificación y mensajes de error de procesamiento documental."""

from __future__ import annotations

import re
from enum import StrEnum


class DocumentErrorCode(StrEnum):
    """Motivo estructurado por el que un documento no se pudo procesar.

    Se persiste en `invoices.error_code`, `tickets.error_code` y
    `document_processing_attempts.error_code`. Sustituye a inferir el motivo
    buscando subcadenas en el texto del error, que era frágil y no permitía
    decidir de forma fiable si un reintento tiene sentido.
    """

    too_many_pages = "too_many_pages"
    image_too_large = "image_too_large"
    unreadable_file = "unreadable_file"
    file_too_large = "file_too_large"
    unsupported_type = "unsupported_type"
    extraction_failed = "extraction_failed"


# Rechazos que dependen del fichero, no del momento: reintentar con el mismo
# fichero volvería a fallar y consumiría cuota. La UI oculta el botón y el
# procesado solo puede desbloquearlo el superadmin.
NON_RETRYABLE_ERROR_CODES: frozenset[DocumentErrorCode] = frozenset(
    {
        DocumentErrorCode.too_many_pages,
        DocumentErrorCode.image_too_large,
        DocumentErrorCode.unreadable_file,
        DocumentErrorCode.file_too_large,
        DocumentErrorCode.unsupported_type,
    },
)

_ADMIN_CONTACT_HINT = (
    "Ponte en contacto con el administrador del sitio para gestionar su procesado."
)

_REJECTION_REASONS: dict[DocumentErrorCode, str] = {
    DocumentErrorCode.too_many_pages: "El documento tiene más páginas de las admitidas.",
    DocumentErrorCode.image_too_large: "La imagen tiene una resolución demasiado grande.",
    DocumentErrorCode.unreadable_file: "El archivo está dañado o no se puede leer.",
    DocumentErrorCode.file_too_large: "El archivo supera el tamaño máximo permitido (20 MB).",
    DocumentErrorCode.unsupported_type: "El formato del archivo no es compatible.",
    DocumentErrorCode.extraction_failed: "No se pudieron extraer los datos del documento.",
}


def is_retryable(error_code: str | None) -> bool:
    """True si reintentar el procesado del mismo fichero puede dar otro resultado."""
    if not error_code:
        return True
    try:
        code = DocumentErrorCode(error_code)
    except ValueError:
        return True
    return code not in NON_RETRYABLE_ERROR_CODES


def rejection_message(
    error_code: DocumentErrorCode,
    *,
    filename: str | None,
    detail: str | None = None,
) -> str:
    """Mensaje de rechazo listo para la UI, con el motivo y la vía de escape.

    Args:
        error_code: Motivo estructurado del rechazo.
        filename: Nombre original del fichero, para que el usuario lo identifique.
        detail: Concreción del motivo (p. ej. "12 páginas; el máximo son 3").
    """
    display_name = (filename or "").strip() or "documento"
    reason = _REJECTION_REASONS.get(
        error_code, _REJECTION_REASONS[DocumentErrorCode.unreadable_file]
    )
    parts = [f'Error al procesar el documento "{display_name}".', reason]
    if detail:
        parts.append(f"({detail}).")
    if error_code in NON_RETRYABLE_ERROR_CODES:
        parts.append(_ADMIN_CONTACT_HINT)
    return " ".join(parts)


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


def failure_message(
    raw_error: str,
    *,
    error_code: DocumentErrorCode,
    filename: str | None,
    detail: str | None = None,
) -> str:
    """Mensaje de UI para un fallo, según haya motivo estructurado o no.

    `extraction_failed` es el cajón de sastre del pipeline LLM: ahí el texto
    técnico sí aporta pistas (qué campo no validó) y se traduce. El resto de
    códigos son rechazos deterministas con mensaje propio.
    """
    if error_code is DocumentErrorCode.extraction_failed:
        return format_user_processing_error(raw_error, filename=filename)[:2000]
    return rejection_message(error_code, filename=filename, detail=detail)[:2000]
