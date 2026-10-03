"""Validación del adjunto del formulario de soporte técnico (admin → SADM).

Un único fichero opcional: Word (.docx), texto (.txt) o imagen JPEG, máx. 2 MB.
El tipo se decide por el CONTENIDO y además debe coincidir con la extensión: así
un ejecutable renombrado a .jpg o un .docm renombrado a .docx no llegan al buzón
del SADM. El fichero no se persiste: se adjunta al email desde memoria.
"""

from __future__ import annotations

import io
import unicodedata
import zipfile

from app.core.email import EmailAttachment
from app.core.uploads import UploadValidationError, original_upload_filename

SUPPORT_ATTACHMENT_MAX_BYTES = 2 * 1024 * 1024

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TXT_MIME = "text/plain"
JPEG_MIME = "image/jpeg"

# .doc (Word 97-2003, binario OLE) excluido a propósito: puede llevar macros.
SUPPORT_ATTACHMENT_EXTENSIONS: dict[str, str] = {
    ".docx": DOCX_MIME,
    ".txt": TXT_MIME,
    ".jpg": JPEG_MIME,
    ".jpeg": JPEG_MIME,
}
# Valor del atributo accept del <input type="file">.
SUPPORT_ATTACHMENT_ACCEPT = ",".join(SUPPORT_ATTACHMENT_EXTENSIONS)

# Códigos de error estables: la ruta los traduce a mensajes para el usuario.
ERR_EMPTY = "attachment_empty"
ERR_TOO_LARGE = "attachment_too_large"
ERR_EXTENSION = "attachment_extension"
ERR_CONTENT = "attachment_content_mismatch"


class SupportAttachmentError(UploadValidationError):
    """Adjunto rechazado; ``code`` identifica el motivo."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _extension(filename: str) -> str:
    _, dot, ext = filename.rpartition(".")
    return f".{ext.lower()}" if dot else ""


def _safe_filename(filename: str | None) -> str:
    """Basename sin caracteres de control (van a la cabecera Content-Disposition)."""
    name = original_upload_filename(filename)
    cleaned = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    return cleaned[:150] or "adjunto"


def _is_docx(data: bytes) -> bool:
    if not zipfile.is_zipfile(io.BytesIO(data)):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile:
        return False
    return (
        "[Content_Types].xml" in names
        and "word/document.xml" in names
        and "word/vbaProject.bin" not in names
    )


def _is_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            data.decode(encoding)
        except UnicodeDecodeError:
            continue
        return True
    return False


def _content_matches(mime: str, data: bytes) -> bool:
    if mime == JPEG_MIME:
        return data[:3] == b"\xff\xd8\xff"
    if mime == DOCX_MIME:
        return _is_docx(data)
    return _is_text(data)


def validate_support_attachment(filename: str | None, data: bytes) -> EmailAttachment:
    """Valida el adjunto y lo devuelve listo para ``send_email``.

    Raises:
        SupportAttachmentError: vacío, >2 MB, extensión no admitida o contenido
            que no corresponde a la extensión.
    """
    if not data:
        raise SupportAttachmentError(ERR_EMPTY)
    if len(data) > SUPPORT_ATTACHMENT_MAX_BYTES:
        raise SupportAttachmentError(ERR_TOO_LARGE)
    safe_name = _safe_filename(filename)
    mime = SUPPORT_ATTACHMENT_EXTENSIONS.get(_extension(safe_name))
    if mime is None:
        raise SupportAttachmentError(ERR_EXTENSION)
    if not _content_matches(mime, data):
        raise SupportAttachmentError(ERR_CONTENT)
    return EmailAttachment(filename=safe_name, content_type=mime, data=data)
