"""La clasificación LLM optimiza la imagen antes de enviarla, igual que la extracción.

Sin esto, una foto de móvil se enviaba en base64 a tamaño completo y girada: el
mismo documento costaba más al clasificar que al extraer y el modelo lo leía de
lado.
"""

from __future__ import annotations

import base64
import io
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.llm.classification import classify_document_with_llm
from app.llm.client import LLMCompleteResult
from app.schemas.document_classification import DocumentTypeClassification
from PIL import Image

pytestmark = pytest.mark.asyncio

TENANT_ID = uuid4()


def _jpeg_bytes(width: int, height: int, *, orientation: int | None = None) -> bytes:
    # Ruido determinista: una imagen plana comprime tanto que no se distinguiría
    # el original del optimizado por tamaño.
    image = Image.new("RGB", (width, height))
    image.putdata(
        [
            ((x * 7) % 256, (y * 13) % 256, (x * y) % 256)
            for y in range(height)
            for x in range(width)
        ]
    )
    buffer = io.BytesIO()
    if orientation is None:
        image.save(buffer, format="JPEG", quality=95)
    else:
        exif = image.getexif()
        exif[0x0112] = orientation
        image.save(buffer, format="JPEG", quality=95, exif=exif)
    return buffer.getvalue()


def _mock_client(captured: list[list[dict[str, Any]]]) -> AsyncMock:
    async def fake_complete(**kwargs: Any) -> LLMCompleteResult[DocumentTypeClassification]:
        captured.append(kwargs["messages"])
        return LLMCompleteResult(
            result=DocumentTypeClassification(
                doc_type="ticket",
                confidence=0.9,
                reason="Importe total sin base imponible desglosada.",
            ),
            llm_call_id=uuid4(),
        )

    client = AsyncMock()
    client.complete = fake_complete
    return client


def _sent_image_bytes(messages: list[dict[str, Any]]) -> bytes:
    media = messages[1]["content"][0]
    return base64.b64decode(media.data)


async def test_classification_downscales_the_photo_before_sending_it() -> None:
    original = _jpeg_bytes(2400, 1600)
    captured: list[list[dict[str, Any]]] = []

    with patch("app.llm.classification.get_llm_client", return_value=_mock_client(captured)):
        await classify_document_with_llm(
            file_bytes=original,
            mime_type="image/jpeg",
            tenant_id=TENANT_ID,
            db=AsyncMock(),
        )

    sent = _sent_image_bytes(captured[0])
    assert len(sent) < len(original)
    with Image.open(io.BytesIO(sent)) as image:
        assert max(image.size) <= 1280


async def test_classification_straightens_a_photo_taken_sideways() -> None:
    captured: list[list[dict[str, Any]]] = []

    with patch("app.llm.classification.get_llm_client", return_value=_mock_client(captured)):
        await classify_document_with_llm(
            file_bytes=_jpeg_bytes(60, 40, orientation=6),
            mime_type="image/jpeg",
            tenant_id=TENANT_ID,
            db=AsyncMock(),
        )

    with Image.open(io.BytesIO(_sent_image_bytes(captured[0]))) as image:
        assert image.size == (40, 60)


async def test_classification_sends_pdfs_untouched() -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(8):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    original = buffer.getvalue()

    captured: list[list[dict[str, Any]]] = []
    with patch("app.llm.classification.get_llm_client", return_value=_mock_client(captured)):
        await classify_document_with_llm(
            file_bytes=original,
            mime_type="application/pdf",
            tenant_id=TENANT_ID,
            db=AsyncMock(),
        )

    assert base64.b64decode(captured[0][1]["content"][0].data) == original
