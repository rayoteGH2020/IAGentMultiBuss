"""Tests de extracción con LLM mockeado.

Estos tests verifican la lógica de extract_invoice() (construcción de mensajes,
validaciones previas al LLM) sin hacer llamadas reales a la API. El mock devuelve
un Factura fijo para aislar el comportamiento del código de los resultados del modelo.
"""

from collections.abc import Callable, Coroutine
from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.core.document_processing_errors import DocumentErrorCode
from app.core.media_limits import MediaLimitExceeded
from app.llm.client import LLMCompleteResult
from app.llm.extraction import PROMPT_VERSION, extract_invoice
from app.models import Tenant
from app.schemas.invoice import Factura
from pypdf import PdfWriter
from sqlalchemy.ext.asyncio import AsyncSession


def _single_page_pdf() -> bytes:
    """PDF real: la inspección previa rechaza cualquier PDF que no pueda leer."""
    import io

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_extract_invoice_calls_llm_with_correct_args(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()

    # Factura válida que el mock devolverá como si viniera del LLM.
    # Los valores numéricos deben ser coherentes (base + iva == total) para
    # que el model_validator de Factura no penalice la confidence.
    fake_factura = Factura(
        fecha=date(2025, 1, 15),
        proveedor="Acme S.L.",
        cif_nif="B00000000",
        base_imponible=Decimal("100.00"),
        iva_percent=Decimal("21.00"),
        iva_amount=Decimal("21.00"),
        total=Decimal("121.00"),
        confidence=0.95,
    )

    # AsyncMock: necesario porque LLMClient.complete() es una coroutine;
    # un Mock normal no sería awaitable y lanzaría TypeError.
    mock_client = AsyncMock()
    llm_call_id = uuid4()
    mock_client.complete = AsyncMock(
        return_value=LLMCompleteResult(result=fake_factura, llm_call_id=llm_call_id),
    )

    # Se parchea la referencia en app.llm.extraction (no en app.llm.client),
    # porque Python resuelve el nombre get_llm_client en el módulo que lo importa.
    # Parchear el módulo original no afectaría a la copia ya importada.
    with patch("app.llm.extraction.get_llm_client", return_value=mock_client):
        result = await extract_invoice(
            file_bytes=_single_page_pdf(),
            mime_type="application/pdf",
            tenant_id=tenant.id,
            db=db_session,
            source_filename="mi-factura.pdf",
        )

    assert result.factura.proveedor == "Acme S.L."
    assert result.llm_call_id == llm_call_id
    # assert_awaited_once(): verifica que complete() fue llamado como coroutine
    # (await) y exactamente una vez. assert_called_once() no distinguiría entre
    # llamada síncrona y awaitable, lo que daría un falso positivo.
    mock_client.complete.assert_awaited_once()
    call_kw = mock_client.complete.await_args
    assert call_kw is not None
    kwargs = call_kw.kwargs

    # Verificaciones de contrato: extract_invoice debe llamar a complete()
    # con los argumentos exactos que el cliente LLM y el eval runner esperan.
    assert kwargs["task"] == "extraction"
    assert kwargs["response_model"] is Factura
    # prompt_version se registra en llm_calls para correlacionar resultados
    # con la versión del prompt en el dashboard de métricas.
    assert kwargs["prompt_version"] == PROMPT_VERSION
    assert kwargs["source_filename"] == "mi-factura.pdf"

    # Verificación de la estructura del mensaje multimodal:
    # [0] system: prompt versionado con instrucciones y schema
    # [1] user: [media_part, instrucción_textual] — el documento va primero.
    msgs = kwargs["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert isinstance(msgs[1]["content"], list)
    # 2 elementos: el objeto PDF/Image (media) y la instrucción textual.
    assert len(msgs[1]["content"]) == 2


@pytest.mark.asyncio
async def test_extract_invoice_rejects_huge_files(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    # 21 MB supera el límite de 20 MB de la API multimodal de Anthropic.
    # El test verifica que extract_invoice rechaza antes de llamar al LLM,
    # evitando el coste de red de subir un fichero que se rechazaría igualmente.
    huge = b"x" * (21 * 1024 * 1024)
    with pytest.raises(MediaLimitExceeded) as exc_info:
        await extract_invoice(
            file_bytes=huge,
            mime_type="application/pdf",
            tenant_id=tenant.id,
            db=db_session,
        )
    assert exc_info.value.error_code is DocumentErrorCode.file_too_large


@pytest.mark.asyncio
async def test_extract_invoice_rejects_bad_mime(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    # application/zip no está en la lista de tipos aceptados por _media_part().
    # El test verifica que el error se lanza en la capa llm (no en uploads.py),
    # ya que extract_invoice recibe el MIME del llamador (el worker) sin
    # pasar de nuevo por validate_invoice_upload.
    with pytest.raises(MediaLimitExceeded) as exc_info:
        await extract_invoice(
            file_bytes=b"hello",
            mime_type="application/zip",
            tenant_id=tenant.id,
            db=db_session,
        )
    assert exc_info.value.error_code is DocumentErrorCode.unsupported_type


@pytest.mark.asyncio
async def test_extract_invoice_rejects_unreadable_pdf(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    """Fail-closed: si no se puede medir el PDF, no se envía al LLM a ciegas."""
    tenant = await tenant_factory()
    with pytest.raises(MediaLimitExceeded) as exc_info:
        await extract_invoice(
            file_bytes=b"%PDF-1.4 esto no es un PDF valido",
            mime_type="application/pdf",
            tenant_id=tenant.id,
            db=db_session,
        )
    assert exc_info.value.error_code is DocumentErrorCode.unreadable_file
