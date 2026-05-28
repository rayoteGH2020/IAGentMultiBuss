"""Validación de subidas de documentos de conocimiento RAG (Paso 18 + Paso 22).

Sigue el mismo patrón que uploads.py (facturas) pero adaptado para los tipos
de fichero del pipeline RAG: PDF digital, texto plano, Markdown e imágenes.
Las imágenes (JPEG, PNG, WebP) se procesan con OCR vía LLM multimodal antes
de ser chunkificadas — mismo modelo que la extracción de facturas (Paso 22).

Detección de tipo en dos capas (igual que uploads.py):
  1. python-magic (libmagic): análisis profundo del contenido binario.
  2. Firmas manuales (_mime_from_signatures): fallback para entornos sin libmagic.
"""

from __future__ import annotations

import structlog

from app.core.uploads import UploadValidationError, original_upload_filename

logger = structlog.get_logger(__name__)

__all__ = [
    "UploadValidationError",
    "original_upload_filename",
    "validate_knowledge_upload",
]


def _mime_from_signatures(data: bytes) -> str | None:
    """Detecta tipo por firmas de fichero sin dependencias externas.

    Casos soportados:
    - PDF: firma `%PDF` en los primeros 4 bytes.
    - JPEG: marcador SOI 0xFF 0xD8 0xFF.
    - PNG: firma de 8 bytes 0x89 PNG \\r\\n 0x1A \\n.
    - WebP: contenedor RIFF con identificador "WEBP" en bytes 8-11.
    - Texto (plain / markdown): ausencia de bytes nulos + decodificable UTF-8.
      Markdown no tiene firma propia; su MIME se resuelve como text/plain y es
      aceptado porque text/plain está en ALLOWED_MIMES de settings.
    """
    # PDF: cabecera "%PDF" (0x25 0x50 0x44 0x46), definida en PDF spec §7.5.2.
    if len(data) >= 4 and data[:4] == b"%PDF":
        return "application/pdf"
    # JPEG: marcador SOI (Start of Image) 0xFF 0xD8 seguido de 0xFF.
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    # PNG: firma de 8 bytes definida en PNG spec §5.2.
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    # WebP: contenedor RIFF con identificador "WEBP" en bytes 8-11.
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # Texto: sin bytes nulos en los primeros 512 bytes y decodificable como UTF-8.
    # Los ficheros binarios (.exe, .zip) casi siempre contienen bytes nulos.
    if len(data) >= 1 and b"\x00" not in data[:512]:
        try:
            data[:512].decode("utf-8")
            return "text/plain"
        except UnicodeDecodeError:
            pass
    return None


def validate_knowledge_upload(
    _filename: str,
    data: bytes,
    max_size_bytes: int,
    allowed_mimes: list[str],
) -> str:
    """Valida un fichero de conocimiento y devuelve su MIME detectado.

    La detección se basa exclusivamente en el contenido del fichero (no en la
    extensión) para prevenir que un fichero malicioso renombrado como .pdf o .md
    supere la validación.

    Args:
        _filename: Nombre original del fichero. No se usa para detección de tipo
            (prefijo _ lo indica); sí puede usarse en mensajes de error si es necesario.
        data: Bytes completos del fichero leídos en memoria.
        max_size_bytes: Límite de tamaño proveniente de settings.knowledge_max_file_size_bytes.
        allowed_mimes: Lista de MIMEs permitidos de settings.knowledge_allowed_mimes.

    Returns:
        MIME type detectado y permitido (p. ej. ``"application/pdf"``).

    Raises:
        UploadValidationError: si el fichero está vacío, supera el límite de
            tamaño o su tipo no está en ``allowed_mimes``.
    """
    allowed = frozenset(allowed_mimes)

    if len(data) == 0:
        msg = "Empty file"
        raise UploadValidationError(msg)
    if len(data) > max_size_bytes:
        msg = f"File too large: {len(data)} bytes (max {max_size_bytes})"
        raise UploadValidationError(msg)

    detected: str | None = None
    try:
        # Import lazy: libmagic puede no estar disponible en CI o Docker mínimo.
        import magic

        detected = magic.from_buffer(data[:4096], mime=True)
    except Exception as exc:
        logger.warning("knowledge_upload.magic_failed", error=str(exc))
        detected = None

    if detected not in allowed:
        # Fallback por firmas manuales. También cubre el caso en que magic devuelve
        # "text/plain" para un fichero markdown que se subió como text/x-markdown.
        fallback = _mime_from_signatures(data)
        if fallback in allowed:
            if detected != fallback:
                logger.info(
                    "knowledge_upload.mime_fallback",
                    detected_by_magic=detected,
                    fallback=fallback,
                )
            detected = fallback

    if detected not in allowed:
        msg = f"Unsupported file type: {detected or 'unknown'}"
        raise UploadValidationError(msg)

    return detected
