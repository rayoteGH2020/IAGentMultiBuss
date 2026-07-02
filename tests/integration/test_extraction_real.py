"""Test de extracción contra la API real de Gemini. Gateado por RUN_LLM_TESTS=1.

Por qué está gateado:
- Tiene coste real (llamada a Gemini Flash, ~0.002 EUR por ejecución).
- Requiere GOOGLE_API_KEY inyectada por Infisical.
- La latencia de la API hace que el test tarde 5-20 s.
- CI lo omite por defecto; los ingenieros lo ejecutan localmente antes de
  cambios en el prompt o en el cliente LLM.

Qué verifica que los tests mockeados no pueden:
- La API de Gemini acepta el PDF en el formato que construimos.
- El modelo extrae campos con confianza razonable (>0.5).
- El registro LLMCall se persiste correctamente en BD tras la llamada real.
"""

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

pytestmark = [pytest.mark.integration, pytest.mark.real_llm]


@pytest.mark.skipif(
    # La comparación estricta "!= '1'" evita que valores como "true" o "yes"
    # activen accidentalmente el test con coste.
    os.getenv("RUN_LLM_TESTS", "").strip() != "1",
    reason="set RUN_LLM_TESTS=1 to enable live LLM extraction tests",
)
@pytest.mark.asyncio
async def test_extract_real_invoice_pdf(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Awaitable[Tenant]],
    # llm_calls_schema_ready: garantiza que la tabla llm_calls existe antes
    # de intentar insertar el registro de observabilidad.
    llm_calls_schema_ready: None,
) -> None:
    pdf_path = FIXTURES_DIR / "ejemplo_01.pdf"
    if not pdf_path.is_file():
        pytest.skip(f"Fixture missing: {pdf_path}")

    # Limpiar el cache de settings para leer la API key del entorno actual
    # (inyectada por Infisical), no la de una ejecución anterior del proceso.
    get_settings.cache_clear()
    if not get_settings().google_api_key.get_secret_value():
        # Skip elegante en lugar de fallo críptico de autenticación de la API.
        pytest.skip("GOOGLE_API_KEY empty; inject via Infisical for Gemini extraction.")

    # reset_llm_client_for_tests(): el cliente LLM es un singleton; puede tener
    # claves vacías de un test anterior. Se resetea para que se reconstruya con
    # las claves actuales del entorno.
    reset_llm_client_for_tests()

    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    try:
        file_bytes = pdf_path.read_bytes()
        extraction = await extract_invoice(
            file_bytes=file_bytes,
            mime_type="application/pdf",
            tenant_id=tenant.id,
            db=db_session,
        )
        factura = extraction.factura
        # commit(): persiste el LLMCall que extract_invoice añadió a la sesión.
        # Sin commit, la verificación de LLMCall a continuación no encontraría
        # el registro (aún estaría solo en la transacción abierta).
        await db_session.commit()

        # Aserciones mínimas sobre la extracción real: no sabemos qué valores
        # exactos devolverá el LLM (depende del PDF), pero sí sabemos que
        # un PDF de factura real debe tener proveedor, CIF y total positivo.
        assert factura.proveedor
        assert factura.cif_nif
        assert factura.total > 0
        # confidence > 0.5: el LLM debe estar al menos moderadamente seguro.
        # Una factura clara debería producir confidence cercana a 1.
        assert factura.confidence > 0.5

        # Verificación del sistema de observabilidad: la llamada real al LLM
        # debe haber creado un LLMCall en BD. rows[-1] toma el más reciente
        # en caso de que haya registros de tests anteriores en la misma BD.
        stmt = select(LLMCall).where(
            LLMCall.task == "extraction",
            LLMCall.prompt_version == "extraction_v1",
        )
        rows = (await db_session.execute(stmt)).scalars().all()
        assert rows, "expected llm_calls row for extraction"
        assert rows[-1].status == "ok"
    finally:
        # Limpiar en finally para no dejar el cliente en estado de test
        # aunque el test haya fallado, evitando contaminar otros tests.
        reset_llm_client_for_tests()
        get_settings.cache_clear()
