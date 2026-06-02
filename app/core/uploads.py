"""Validación de subidas de facturas (tamaño, MIME por magic bytes + firmas simples).

La detección de tipo se hace en dos capas:
  1. python-magic (libmagic): análisis profundo del contenido binario.
  2. Firmas manuales (_mime_from_signatures): fallback para entornos sin libmagic
     (CI minimalista, Docker sin libmagic-dev instalado).

Ambas capas se basan en el CONTENIDO del fichero, no en su extensión. Esto
previene que un atacante renombre `malware.exe` a `factura.pdf` y lo suba.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)

# 20 MB: límite superior de la API multimodal de Anthropic para contenido
# embebido en base64. Coincide también con el límite validado en extract_invoice().
# Expresado como producto para que el número sea legible sin calculadora.
MAX_FILE_SIZE = 20 * 1024 * 1024

# frozenset (no set): inmutable, sin riesgo de modificación accidental en runtime.
# La búsqueda `in` es O(1), igual que en set.
# IMPORTANTE: estos tipos deben coincidir exactamente con los aceptados por
# _media_part() en app/llm/extraction.py y _mime_for() en el eval runner.
# Si se añade un tipo aquí, hay que añadirlo también en esas funciones.
ALLOWED_MIMES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/webp",
    },
)


def original_upload_filename(filename: str | None) -> str:
    """Nombre original del fichero para persistir en BD (sin rutas, máx. 300 chars)."""
    raw = (filename or "").strip()
    if not raw:
        return "sin-nombre"
    basename = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return basename[:300] if basename else "sin-nombre"


class UploadValidationError(Exception):
    """Fallo de validación antes de persistir ni subir a R2.

    Excepción propia (no ValidationError de core.errors) porque el route de
    upload la captura por fichero individual para acumular errores parciales
    (patrón de éxito parcial: 3 de 5 ficheros válidos). Si fuera ValidationError
    aborataría el request completo en lugar de continuar con los siguientes.
    """


def _mime_from_signatures(data: bytes) -> str | None:
    """Detección de tipo por firmas de fichero (magic bytes), sin dependencias externas.

    Las firmas (primeros bytes de un fichero) están definidas en las especificaciones
    de cada formato y son muy difíciles de falsificar mientras el fichero sea válido.
    Se usa como fallback cuando python-magic no está disponible en el entorno.
    """
    # PDF: cabecera "%PDF" (0x25 0x50 0x44 0x46), primeros 4 bytes.
    # Definida en la especificación PDF §7.5.2 (File Header).
    if len(data) >= 4 and data[:4] == b"%PDF":
        return "application/pdf"
    # JPEG: marcador SOI (Start of Image) 0xFF 0xD8, seguido del inicio del
    # siguiente marcador 0xFF. Los 3 bytes juntos identifican JPEG sin ambigüedad.
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    # PNG: firma de 8 bytes definida en PNG spec §5.2: los bytes 137 80 78 71
    # 13 10 26 10. El byte inicial (0x89) > 127 detecta transferencias ASCII
    # incorrectas; los bytes \r\n\x1a\n detectan conversiones de salto de línea.
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    # WebP: contenedor RIFF con identificador "WEBP" en bytes 8-11.
    # Los primeros 4 bytes siempre son "RIFF" (formato contenedor genérico);
    # los bytes 8-11 identifican el subtipo concreto dentro de RIFF.
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_invoice_upload(_filename: str, data: bytes) -> str:
    """Devuelve el MIME permitido tras validar tamaño y tipo.

    Args:
        _filename: Nombre del fichero (prefijo _ indica que no se usa para
            detectar el tipo; la detección es solo por contenido, no extensión).
        data: Bytes completos del fichero ya leídos en memoria.

    Returns:
        MIME type detectado y permitido (p. ej. "application/pdf").

    Raises:
        UploadValidationError: si el fichero está vacío, supera el límite de
            tamaño o su tipo no está en ALLOWED_MIMES.
    """
    # Comprobación de vacío antes que de tamaño: evita un mensaje de error
    # confuso ("demasiado grande: 0 bytes") y es la validación más barata.
    if len(data) == 0:
        msg = "Empty file"
        raise UploadValidationError(msg)
    # Comprobación de tamaño antes de MIME: no tiene sentido analizar el
    # contenido de un fichero que ya sabemos que vamos a rechazar.
    if len(data) > MAX_FILE_SIZE:
        msg = f"File too large: {len(data)} bytes (max {MAX_FILE_SIZE})"
        raise UploadValidationError(msg)

    detected: str | None = None
    try:
        # Import dentro del try: python-magic requiere libmagic instalado en el
        # sistema operativo (libmagic-dev en Debian/Ubuntu). Si la librería no
        # está disponible, ImportError llega aquí y se usa el fallback de firmas.
        # Lazy import también evita que un entorno sin libmagic falle al arrancar.
        import magic

        # Solo los primeros 4096 bytes: libmagic no necesita el fichero completo
        # para detectar el tipo; limitar el buffer evita pasar hasta 20 MB a la
        # librería nativa, reduciendo tiempo y memoria.
        # mime=True: devuelve "application/pdf" en lugar de "PDF document, version 1.7".
        detected = magic.from_buffer(data[:4096], mime=True)
    except Exception as exc:
        # Captura amplia (no solo ImportError): libmagic puede fallar también
        # por problemas de la base de datos de firmas o permisos del SO.
        # Se loguea como warning (no error) porque el fallback puede resolverlo.
        logger.warning("upload.magic_failed", error=str(exc))
        detected = None

    if detected not in ALLOWED_MIMES:
        # Segundo intento con firmas manuales. Se ejecuta cuando:
        # a) python-magic no está disponible (detected=None), o
        # b) magic detectó un tipo no permitido (p. ej. "application/octet-stream"
        #    para un PDF en algunos entornos con libmagic desactualizado).
        fallback = _mime_from_signatures(data)
        if fallback in ALLOWED_MIMES:
            if detected != fallback:
                # Discrepancia entre magic y firmas: útil para detectar ficheros
                # cuya cabecera es válida pero que magic cataloga diferente.
                # Se loguea como info, no warning: el fichero se acepta.
                logger.info(
                    "upload.mime_fallback",
                    detected_by_magic=detected,
                    fallback=fallback,
                )
            detected = fallback

    # Segunda comprobación: cubre el caso en que tanto magic como el fallback
    # de firmas fallaron (fichero de tipo desconocido o no soportado).
    # Se comprueba de nuevo en lugar de anidarlo en el bloque anterior para
    # que la lógica sea lineal y fácil de seguir.
    if detected not in ALLOWED_MIMES:
        msg = f"Unsupported file type: {detected or 'unknown'}"
        raise UploadValidationError(msg)
    return detected


# ---------------------------------------------------------------------------
# Validación de audio para voz → Google Calendar (Paso 23)
# ---------------------------------------------------------------------------

ALLOWED_AUDIO_MIMES: frozenset[str] = frozenset(
    {
        "audio/ogg",
        "audio/mpeg",
        "audio/mp4",
        "audio/aac",
        "audio/wav",
        "audio/webm",
    }
)

# Aliases que python-magic o algunos navegadores reportan para el mismo formato.
# La normalización ocurre antes de comprobar contra ALLOWED_AUDIO_MIMES.
_AUDIO_MIME_ALIASES: dict[str, str] = {
    "audio/x-wav": "audio/wav",
    "audio/x-mpeg": "audio/mpeg",
    "audio/x-ogg": "audio/ogg",
    # MediaRecorder genera audio en contenedor WebM; magic lo detecta como
    # video/webm porque analiza el tipo de pista EBML, no la intención de uso.
    "video/webm": "audio/webm",
}


def _audio_mime_from_signatures(data: bytes) -> str | None:
    """Detección de tipo de audio por magic bytes, sin dependencias externas."""
    # OGG: cabecera "OggS" (RFC 3533 §6).
    if len(data) >= 4 and data[:4] == b"OggS":
        return "audio/ogg"
    # MP3 con etiqueta ID3 (cabecera "ID3", ISO/IEC 11172-3).
    if len(data) >= 3 and data[:3] == b"ID3":
        return "audio/mpeg"
    # MP3 sin ID3: sync word 0xFF seguido de 0xFB / 0xF3 / 0xF2.
    if len(data) >= 2 and data[0] == 0xFF and data[1] in (0xFB, 0xF3, 0xF2):
        return "audio/mpeg"
    # WAV: contenedor RIFF con identificador "WAVE" en bytes 8-11.
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    # MP4 / M4A: caja ISO Base Media File Format; "ftyp" siempre en bytes 4-7.
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return "audio/mp4"
    # AAC (ADTS): sync word 0xFF 0xF1 (MPEG-4) o 0xFF 0xF9 (MPEG-2).
    if len(data) >= 2 and data[0] == 0xFF and data[1] in (0xF1, 0xF9):
        return "audio/aac"
    # WebM / MKV: cabecera EBML 0x1A 0x45 0xDF 0xA3.
    if len(data) >= 4 and data[:4] == b"\x1a\x45\xdf\xa3":
        return "audio/webm"
    return None


def validate_voice_upload(data: bytes, *, max_bytes: int) -> str:
    """Valida tamaño y tipo de un fichero de audio para dictado de eventos.

    Args:
        data: Bytes completos del audio leídos en memoria.
        max_bytes: Límite de tamaño en bytes (de settings.voice_max_audio_bytes).

    Returns:
        MIME type normalizado y permitido (p. ej. ``"audio/ogg"``).

    Raises:
        UploadValidationError: si el fichero está vacío, supera el límite o
            su tipo no está en ALLOWED_AUDIO_MIMES.

    Note:
        La validación de duración (voice_max_audio_seconds) es específica de
        cada contenedor y requiere parsear metadatos del formato. El límite de
        bytes actúa como cota superior suficiente para el MVP.
    """
    if len(data) == 0:
        raise UploadValidationError("Empty audio file")
    if len(data) > max_bytes:
        raise UploadValidationError(f"Audio too large: {len(data)} bytes (max {max_bytes})")

    detected: str | None = None
    try:
        import magic

        detected = magic.from_buffer(data[:4096], mime=True)
    except Exception as exc:
        logger.warning("upload.audio_magic_failed", error=str(exc))
        detected = None

    # Normalizar aliases antes de comprobar la lista permitida.
    if detected is not None:
        detected = _AUDIO_MIME_ALIASES.get(detected, detected)

    if detected not in ALLOWED_AUDIO_MIMES:
        fallback = _audio_mime_from_signatures(data)
        if fallback in ALLOWED_AUDIO_MIMES:
            if detected != fallback:
                logger.info(
                    "upload.audio_mime_fallback",
                    detected_by_magic=detected,
                    fallback=fallback,
                )
            detected = fallback

    if detected not in ALLOWED_AUDIO_MIMES:
        raise UploadValidationError(f"Unsupported audio type: {detected or 'unknown'}")
    return detected
