"""Validación de subidas de facturas (tamaño, MIME por magic bytes + firmas simples)."""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)

MAX_FILE_SIZE = 20 * 1024 * 1024
ALLOWED_MIMES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/webp",
    },
)


class UploadValidationError(Exception):
    """Fallo de validación antes de persistir ni subir a R2."""


def _mime_from_signatures(data: bytes) -> str | None:
    """Detección básica sin libmagic (entornos donde falla python-magic)."""
    if len(data) >= 4 and data[:4] == b"%PDF":
        return "application/pdf"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_invoice_upload(_filename: str, data: bytes) -> str:
    """Devuelve el MIME permitido tras validar tamaño y tipo."""
    if len(data) == 0:
        msg = "Empty file"
        raise UploadValidationError(msg)
    if len(data) > MAX_FILE_SIZE:
        msg = f"File too large: {len(data)} bytes (max {MAX_FILE_SIZE})"
        raise UploadValidationError(msg)

    detected: str | None = None
    try:
        import magic

        detected = magic.from_buffer(data[:4096], mime=True)
    except Exception as exc:
        logger.warning("upload.magic_failed", error=str(exc))
        detected = None

    if detected not in ALLOWED_MIMES:
        fallback = _mime_from_signatures(data)
        if fallback in ALLOWED_MIMES:
            if detected != fallback:
                logger.info(
                    "upload.mime_fallback",
                    detected_by_magic=detected,
                    fallback=fallback,
                )
            detected = fallback

    if detected not in ALLOWED_MIMES:
        msg = f"Unsupported file type: {detected or 'unknown'}"
        raise UploadValidationError(msg)
    return detected
