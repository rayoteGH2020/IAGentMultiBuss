"""Extracción contra API real (Gemini / extracción); requiere RUN_LLM_TESTS=1."""

import os
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from app.config import get_settings
from app.core.db import set_tenant_context
from app.llm.client import reset_llm_client_for_tests
from app.llm.extraction import extract_invoice
from app.models import LLMCall, Tenant
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "invoices"

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("RUN_LLM_TESTS", "").strip() != "1",
    reason="set RUN_LLM_TESTS=1 to enable live LLM extraction tests",
)
@pytest.mark.asyncio
async def test_extract_real_invoice_pdf(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Awaitable[Tenant]],
    llm_calls_schema_ready: None,
) -> None:
    pdf_path = FIXTURES_DIR / "ejemplo_01.pdf"
    if not pdf_path.is_file():
        pytest.skip(f"Fixture missing: {pdf_path}")

    get_settings.cache_clear()
    if not get_settings().google_api_key.get_secret_value():
        pytest.skip("GOOGLE_API_KEY empty; inject via Infisical for Gemini extraction.")

    reset_llm_client_for_tests()

    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    try:
        file_bytes = pdf_path.read_bytes()
        factura = await extract_invoice(
            file_bytes=file_bytes,
            mime_type="application/pdf",
            tenant_id=tenant.id,
            db=db_session,
        )
        await db_session.commit()

        assert factura.proveedor
        assert factura.cif_nif
        assert factura.total > 0
        assert factura.confidence > 0.5

        stmt = select(LLMCall).where(
            LLMCall.task == "extraction",
            LLMCall.prompt_version == "extraction_v1",
        )
        rows = (await db_session.execute(stmt)).scalars().all()
        assert rows, "expected llm_calls row for extraction"
        assert rows[-1].status == "ok"
    finally:
        reset_llm_client_for_tests()
        get_settings.cache_clear()
