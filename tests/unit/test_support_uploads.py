"""Adjunto del formulario de soporte: formato por contenido + extensión, máx. 2 MB."""

from __future__ import annotations

import io
import zipfile

import pytest
from app.core.support_uploads import (
    DOCX_MIME,
    ERR_CONTENT,
    ERR_EMPTY,
    ERR_EXTENSION,
    ERR_TOO_LARGE,
    JPEG_MIME,
    SUPPORT_ATTACHMENT_MAX_BYTES,
    TXT_MIME,
    SupportAttachmentError,
    validate_support_attachment,
)

_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _docx(*, extra: dict[str, bytes] | None = None, document: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        if document:
            archive.writestr("word/document.xml", "<w:document/>")
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buf.getvalue()


@pytest.mark.parametrize(
    ("filename", "data", "mime"),
    [
        ("captura.JPG", _JPEG, JPEG_MIME),
        ("captura.jpeg", _JPEG, JPEG_MIME),
        ("informe.docx", _docx(), DOCX_MIME),
        ("log.txt", "Error en la línea 3\n".encode(), TXT_MIME),
        ("notas.txt", "Añadir opción".encode("cp1252"), TXT_MIME),
    ],
)
def test_accepts_allowed_formats(filename: str, data: bytes, mime: str) -> None:
    attachment = validate_support_attachment(filename, data)
    assert attachment.content_type == mime
    assert attachment.data == data
    assert attachment.filename == filename


def test_rejects_empty_file() -> None:
    with pytest.raises(SupportAttachmentError) as exc:
        validate_support_attachment("a.txt", b"")
    assert exc.value.code == ERR_EMPTY


def test_rejects_file_over_2_mb() -> None:
    data = b"a" * (SUPPORT_ATTACHMENT_MAX_BYTES + 1)
    with pytest.raises(SupportAttachmentError) as exc:
        validate_support_attachment("a.txt", data)
    assert exc.value.code == ERR_TOO_LARGE


def test_accepts_exactly_2_mb() -> None:
    data = b"a" * SUPPORT_ATTACHMENT_MAX_BYTES
    assert validate_support_attachment("a.txt", data).content_type == TXT_MIME


@pytest.mark.parametrize("filename", ["legacy.doc", "macro.docm", "run.exe", "foto.png", "sin_ext"])
def test_rejects_other_extensions(filename: str) -> None:
    with pytest.raises(SupportAttachmentError) as exc:
        validate_support_attachment(filename, b"hola")
    assert exc.value.code == ERR_EXTENSION


@pytest.mark.parametrize(
    ("filename", "data"),
    [
        ("foto.jpg", b"MZ\x90\x00 no es jpeg"),
        ("informe.docx", b"PK\x03\x04 zip roto"),
        ("informe.docx", _docx(document=False)),
        ("macro.docx", _docx(extra={"word/vbaProject.bin": b"\x00\x01"})),
        ("binario.txt", b"texto\x00con nulos"),
        ("foto.txt", _JPEG),
    ],
)
def test_rejects_content_not_matching_extension(filename: str, data: bytes) -> None:
    with pytest.raises(SupportAttachmentError) as exc:
        validate_support_attachment(filename, data)
    assert exc.value.code == ERR_CONTENT


def test_filename_strips_paths_and_control_chars() -> None:
    attachment = validate_support_attachment("C:\\tmp\\ma\r\nlo\x00.txt", b"ok")
    assert attachment.filename == "malo.txt"
