"""Tests de extracción con LLM mockeado."""

from collections.abc import Callable, Coroutine
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from app.llm.extraction import PROMPT_VERSION, extract_invoice
from app.models import Tenant
from app.schemas.invoice import Factura
from sqlalchemy.ext.asyncio import AsyncSession

_FIXTURE_PDF = Path(__file__).resolve().parent.parent / "fixtures" / "invoices" / "ejemplo_01.pdf"


@pytest.mark.asyncio
async def test_extract_invoice_calls_llm_with_correct_args(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
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

    mock_client = AsyncMock()
    mock_client.complete = AsyncMock(return_value=fake_factura)

    with patch("app.llm.extraction.get_llm_client", return_value=mock_client):
        result = await extract_invoice(
            file_bytes=_FIXTURE_PDF.read_bytes(),
            mime_type="application/pdf",
            tenant_id=tenant.id,
            db=db_session,
        )

    assert result.proveedor == "Acme S.L."
    mock_client.complete.assert_awaited_once()
    call_kw = mock_client.complete.await_args
    assert call_kw is not None
    kwargs = call_kw.kwargs
    assert kwargs["task"] == "extraction"
    assert kwargs["response_model"] is Factura
    assert kwargs["prompt_version"] == PROMPT_VERSION
    msgs = kwargs["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert isinstance(msgs[1]["content"], list)
    assert len(msgs[1]["content"]) == 2


@pytest.mark.asyncio
async def test_extract_invoice_rejects_huge_files(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    huge = b"x" * (21 * 1024 * 1024)
    with pytest.raises(ValueError, match="too large"):
        await extract_invoice(
            file_bytes=huge,
            mime_type="application/pdf",
            tenant_id=tenant.id,
            db=db_session,
        )


@pytest.mark.asyncio
async def test_extract_invoice_rejects_bad_mime(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    with pytest.raises(ValueError, match="Unsupported mime type"):
        await extract_invoice(
            file_bytes=b"hello",
            mime_type="application/zip",
            tenant_id=tenant.id,
            db=db_session,
        )
