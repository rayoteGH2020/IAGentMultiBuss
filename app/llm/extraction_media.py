"""Preprocesado de medios para extracción de facturas (latencia y tokens).

Antes de tocar el fichero se valida contra los límites de `core/media_limits`:
un PDF de más páginas de las admitidas o una imagen desproporcionada se rechaza
aquí, no se recorta en silencio. Recortar ocultaba al usuario que solo se leyó
parte de su documento; ahora o se procesa entero o se rechaza con un motivo.
"""

from __future__ import annotations

import io

import structlog

from app.core.media_limits import (
    IMAGE_MIMES,
    PDF_MIME,
    MediaInspection,
    inspect_document,
    open_image_within_limits,
)

logger = structlog.get_logger(__name__)

# Las facturas fotografiadas llegan a 4000 px de lado; 1280 px es suficiente
# para que el modelo lea importes y CIF, y recorta tokens y latencia.
_MAX_IMAGE_LONG_EDGE_PX = 1280
_JPEG_QUALITY = 80


def prepare_invoice_media(
    file_bytes: bytes,
    mime_type: str,
    *,
    max_pdf_pages: int | None = None,
) -> tuple[bytes, str, MediaInspection]:
    """Valida límites y devuelve los bytes optimizados para la llamada LLM.

    Args:
        file_bytes: Contenido del documento.
        mime_type: MIME validado en la subida.
        max_pdf_pages: Tope de páginas; `None` usa el límite de negocio.

    Returns:
        Tupla (bytes a enviar, MIME resultante, inspección del original).

    Raises:
        MediaLimitExceeded: El documento supera los límites o es ilegible.
    """
    inspection = inspect_document(file_bytes, mime_type, max_pdf_pages=max_pdf_pages)

    if mime_type == PDF_MIME:
        return file_bytes, mime_type, inspection
    if mime_type in IMAGE_MIMES:
        optimized, optimized_mime = _optimize_image(file_bytes, mime_type)
        return optimized, optimized_mime, inspection
    return file_bytes, mime_type, inspection


def _optimize_image(file_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    image = open_image_within_limits(file_bytes)
    try:
        width, height = image.size
        long_edge = max(width, height)
        if long_edge > _MAX_IMAGE_LONG_EDGE_PX:
            scale = _MAX_IMAGE_LONG_EDGE_PX / long_edge
            image = image.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                _resampling_lanczos(),
            )
            width, height = image.size

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
        optimized = buffer.getvalue()
    finally:
        image.close()

    # Recomprimir una imagen ya pequeña puede engordarla; en ese caso se envía
    # el original, que además conserva mejor calidad para el OCR del modelo.
    if len(optimized) >= len(file_bytes) and long_edge <= _MAX_IMAGE_LONG_EDGE_PX:
        return file_bytes, mime_type

    logger.info(
        "extraction_media.image_optimized",
        original_bytes=len(file_bytes),
        optimized_bytes=len(optimized),
        original_mime=mime_type,
        width=width,
        height=height,
    )
    return optimized, "image/jpeg"


def _resampling_lanczos() -> int:
    from PIL import Image

    return int(Image.Resampling.LANCZOS)
