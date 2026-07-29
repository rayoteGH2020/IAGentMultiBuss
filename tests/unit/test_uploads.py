"""Tests de utilidades de subida."""

from __future__ import annotations

import pytest
from app.core.uploads import UploadValidationError, original_upload_filename, read_upload_limited


class FakeUpload:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if not self._data:
            return b""
        if size < 0:
            chunk = self._data
            self._data = b""
            return chunk
        chunk = self._data[:size]
        self._data = self._data[size:]
        return chunk


def test_original_upload_filename_strips_path() -> None:
    assert original_upload_filename(r"C:\Users\doc\factura.pdf") == "factura.pdf"
    assert original_upload_filename("folder/ticket.jpg") == "ticket.jpg"


def test_original_upload_filename_empty_fallback() -> None:
    assert original_upload_filename(None) == "sin-nombre"
    assert original_upload_filename("   ") == "sin-nombre"


@pytest.mark.asyncio
async def test_read_upload_limited_returns_bytes_within_limit() -> None:
    upload = FakeUpload(b"abcde")

    data = await read_upload_limited(upload, max_bytes=5)

    assert data == b"abcde"


@pytest.mark.asyncio
async def test_read_upload_limited_rejects_as_soon_as_limit_is_exceeded() -> None:
    upload = FakeUpload(b"abcdef")

    with pytest.raises(UploadValidationError, match="File too large"):
        await read_upload_limited(upload, max_bytes=5)

    assert upload.read_sizes == [6]
