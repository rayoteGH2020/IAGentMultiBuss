"""Límites de recursos al inspeccionar y decodificar ficheros subidos.

Un PDF de 200 KB puede declarar 20.000 páginas y una imagen de 40 KB puede
expandirse a 40 GB en memoria al decodificarse (decompression bomb). El worker
ARQ es un proceso compartido por todos los tenants: agotarle la RAM es una
denegación de servicio para todos, no solo para quien subió el fichero.

Este módulo concentra la inspección previa —barata, sobre cabeceras— y la
decodificación acotada. Es **fail-closed**: si no se puede determinar el tamaño
real del contenido, el documento se rechaza en lugar de procesarse a ciegas.

Todas las funciones son síncronas y hacen trabajo intensivo de CPU: los
llamadores desde código async deben envolverlas en `asyncio.to_thread`.
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from app.config import get_settings
from app.core.document_processing_errors import DocumentErrorCode
from app.core.errors import ValidationError

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

logger = structlog.get_logger(__name__)

PDF_MIME = "application/pdf"
IMAGE_MIMES: frozenset[str] = frozenset({"image/jpeg", "image/png", "image/webp"})


class MediaLimitExceeded(ValidationError):
    """El fichero supera un límite de recursos o no se puede inspeccionar."""

    code = "media_limit_exceeded"

    def __init__(
        self,
        message: str,
        *,
        error_code: DocumentErrorCode,
        detail: str | None = None,
    ) -> None:
        super().__init__(message, details={"error_code": error_code.value})
        self.error_code = error_code
        # Concreción legible para el usuario ("12 páginas; el máximo son 3").
        self.detail = detail


@dataclass(frozen=True, slots=True)
class MediaInspection:
    """Tamaño real del contenido, medido sin decodificarlo entero."""

    mime_type: str
    size_bytes: int
    # 1 para imágenes: una imagen es una "página" a efectos de coste LLM.
    pages: int
    width: int | None = None
    height: int | None = None


def inspect_document(
    file_bytes: bytes,
    mime_type: str,
    *,
    max_pdf_pages: int | None = None,
) -> MediaInspection:
    """Mide el documento y rechaza lo que no cabe en los límites configurados.

    Args:
        file_bytes: Contenido completo del fichero.
        mime_type: MIME ya validado contra la lista blanca de subidas.
        max_pdf_pages: Tope de páginas; por defecto `DOCUMENT_MAX_PDF_PAGES`.
            El procesado excepcional autorizado por el superadmin pasa un valor
            mayor, nunca `None`.

    Returns:
        La inspección con páginas y dimensiones reales.

    Raises:
        MediaLimitExceeded: Si supera un límite o no se puede inspeccionar.
    """
    settings = get_settings()
    page_limit = max_pdf_pages if max_pdf_pages is not None else settings.document_max_pdf_pages

    if mime_type == PDF_MIME:
        pages = pdf_page_count(file_bytes)
        if pages > page_limit:
            raise MediaLimitExceeded(
                f"PDF with {pages} pages exceeds limit of {page_limit}",
                error_code=DocumentErrorCode.too_many_pages,
                detail=f"{pages} páginas; el máximo admitido son {page_limit}",
            )
        return MediaInspection(mime_type=mime_type, size_bytes=len(file_bytes), pages=pages)

    if mime_type in IMAGE_MIMES:
        width, height = image_dimensions(file_bytes)
        _enforce_image_dimensions(width, height)
        return MediaInspection(
            mime_type=mime_type,
            size_bytes=len(file_bytes),
            pages=1,
            width=width,
            height=height,
        )

    raise MediaLimitExceeded(
        f"Unsupported mime type for extraction: {mime_type}",
        error_code=DocumentErrorCode.unsupported_type,
    )


def pdf_page_count(file_bytes: bytes) -> int:
    """Número de páginas de un PDF, leyendo solo la tabla de referencias.

    Raises:
        MediaLimitExceeded: PDF corrupto, cifrado con contraseña o ilegible.
    """
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        # Contraseña vacía es el caso habitual de PDFs "protegidos" por bancos
        # y gestorías; con contraseña real no hay nada que hacer.
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise MediaLimitExceeded(
                "Encrypted PDF requires a password",
                error_code=DocumentErrorCode.unreadable_file,
                detail="el PDF está protegido con contraseña",
            )
        return len(reader.pages)
    except MediaLimitExceeded:
        raise
    except (PdfReadError, ValueError, OSError, RecursionError) as exc:
        logger.warning("media_limits.pdf_unreadable", error=str(exc)[:200])
        raise MediaLimitExceeded(
            "Cannot read PDF structure",
            error_code=DocumentErrorCode.unreadable_file,
        ) from exc


def image_dimensions(file_bytes: bytes) -> tuple[int, int]:
    """Dimensiones declaradas en la cabecera, sin decodificar los píxeles.

    `Image.open` es perezoso: parsea la cabecera y no reserva el búfer de
    píxeles hasta `load()`. Por eso se puede rechazar una bomba antes de que
    consuma memoria.
    """
    from PIL import Image, UnidentifiedImageError

    _configure_pillow_limits()
    try:
        with Image.open(io.BytesIO(file_bytes)) as image:
            width, height = image.size
    except Image.DecompressionBombError as exc:
        raise MediaLimitExceeded(
            "Image exceeds Pillow decompression bomb threshold",
            error_code=DocumentErrorCode.image_too_large,
            detail="la resolución de la imagen es desproporcionada",
        ) from exc
    except (UnidentifiedImageError, ValueError, OSError) as exc:
        logger.warning("media_limits.image_unreadable", error=str(exc)[:200])
        raise MediaLimitExceeded(
            "Cannot read image header",
            error_code=DocumentErrorCode.unreadable_file,
        ) from exc
    return width, height


def open_image_within_limits(file_bytes: bytes) -> PILImage:
    """Decodifica una imagen en RGB tras validar sus dimensiones.

    Returns:
        La imagen ya cargada en memoria; el llamador es responsable de cerrarla.

    Raises:
        MediaLimitExceeded: Si supera los límites o no se puede decodificar.
    """
    from PIL import Image, UnidentifiedImageError

    _configure_pillow_limits()
    width, height = image_dimensions(file_bytes)
    _enforce_image_dimensions(width, height)

    try:
        # simplefilter("error"): Pillow avisa con DecompressionBombWarning en
        # lugar de fallar cuando el área supera MAX_IMAGE_PIXELS pero no el
        # doble. Aquí un aviso equivale a un rechazo.
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(file_bytes)) as image_file:
                return image_file.convert("RGB")
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise MediaLimitExceeded(
            "Image exceeds decompression bomb threshold",
            error_code=DocumentErrorCode.image_too_large,
            detail="la resolución de la imagen es desproporcionada",
        ) from exc
    except (UnidentifiedImageError, ValueError, OSError, MemoryError) as exc:
        logger.warning("media_limits.image_decode_failed", error=str(exc)[:200])
        raise MediaLimitExceeded(
            "Cannot decode image",
            error_code=DocumentErrorCode.unreadable_file,
        ) from exc


def _enforce_image_dimensions(width: int, height: int) -> None:
    settings = get_settings()
    max_edge = settings.document_max_image_edge_px
    max_pixels = settings.document_max_image_pixels
    if width > max_edge or height > max_edge:
        raise MediaLimitExceeded(
            f"Image edge {max(width, height)}px exceeds limit of {max_edge}px",
            error_code=DocumentErrorCode.image_too_large,
            detail=f"{width}x{height} px; el lado máximo admitido son {max_edge} px",
        )
    if width * height > max_pixels:
        raise MediaLimitExceeded(
            f"Image area {width * height}px exceeds limit of {max_pixels}px",
            error_code=DocumentErrorCode.image_too_large,
            detail=(
                f"{width}x{height} px; el máximo admitido son {max_pixels // 1_000_000} megapíxeles"
            ),
        )


def _configure_pillow_limits() -> None:
    """Alinea el tope interno de Pillow con el de la aplicación.

    Pillow trae 89 Mpx por defecto y lo aplica al decodificar. Se baja al
    valor configurado para que la segunda barrera coincida con la primera.
    """
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = get_settings().document_max_image_pixels
