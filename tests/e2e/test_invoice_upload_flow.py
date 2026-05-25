"""Test E2E con Playwright: flujo completo de subida de factura. Gateado por RUN_E2E=1.

Qué verifica que los tests de integración no pueden:
- El modal de Alpine.js se abre y responde a clicks reales.
- La subida HTMX multipart funciona desde el navegador real.
- El polling "every 2s" de HTMX se ejecuta y actualiza la fila.
- Todo el pipeline (upload → R2 → ARQ worker → LLM → DB → HTMX polling)
  termina dentro del timeout de 30 segundos.

Prerrequisitos:
- Servidor corriendo en E2E_BASE_URL (por defecto localhost:8000).
- Worker ARQ corriendo y conectado al mismo Redis.
- auth_state.json generado previamente con Playwright (cookies de sesión Clerk).
- GOOGLE_API_KEY configurada (la extracción LLM es real).

Para generar auth_state.json manualmente:
    playwright codegen http://localhost:8000 --save-storage=tests/e2e/auth_state.json
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, expect

# pytest.mark.e2e: permite filtrar con `-m e2e` o excluirlos con `-m "not e2e"`.
pytestmark = pytest.mark.e2e

# E2E_BASE_URL: configurable para apuntar a staging en CI.
BASE_URL = os.getenv("E2E_BASE_URL", "http://localhost:8000")
# AUTH_STATE: fichero JSON con las cookies y tokens de sesión de Clerk,
# grabado con `playwright codegen --save-storage`. Evita pasar por el
# flujo de login de Clerk en cada ejecución del test.
AUTH_STATE = Path(os.getenv("E2E_AUTH_STATE", "tests/e2e/auth_state.json"))
FIXTURE_PDF = Path(__file__).resolve().parents[1] / "fixtures" / "invoices" / "ejemplo_01.pdf"


@pytest.mark.skipif(not os.getenv("RUN_E2E"), reason="set RUN_E2E=1 to enable")
@pytest.mark.asyncio
async def test_upload_invoice_and_see_result() -> None:
    if not AUTH_STATE.exists():
        # Skip con mensaje explicativo: el auth_state debe generarse manualmente
        # una vez por entorno. No es un fallo del test, sino de configuración.
        pytest.skip(f"Falta {AUTH_STATE}; genera la sesión de Clerk con Playwright en dev.")
    if not FIXTURE_PDF.exists():
        pytest.skip(f"Falta fixture {FIXTURE_PDF}")

    async with async_playwright() as p:
        # Chromium: el navegador más compatible con los estándares HTMX y Alpine.
        # En CI puede lanzarse sin cabeza (headless=True es el default).
        browser = await p.chromium.launch()
        try:
            # storage_state: carga las cookies y tokens de Clerk del fichero JSON.
            # El navegador arranca ya autenticado, sin pasar por el flujo de login.
            ctx = await browser.new_context(storage_state=str(AUTH_STATE))
            page = await ctx.new_page()

            # 1. Navegar a /invoices y verificar que la página cargó correctamente.
            await page.goto(f"{BASE_URL}/invoices")
            await expect(page.locator("h1")).to_contain_text("Facturas")

            # 2. Abrir el modal de subida. El botón activa el estado Alpine `open=true`.
            await page.click("text=Subir documentos")

            # 3. Seleccionar el fichero mediante el selector de ficheros del navegador.
            # expect_file_chooser intercepta el diálogo OS que se abriría al
            # hacer click en el label; set_files() proporciona el fichero sin
            # necesitar interacción con el diálogo nativo del SO.
            async with page.expect_file_chooser() as fc_info:
                await page.click("label.border-dashed")
            file_chooser = await fc_info.value
            await file_chooser.set_files(str(FIXTURE_PDF))

            # 3b. Elegir tipo de documento (obligatorio).
            await page.select_option("#doc-type-code", "factura")

            # 4. Enviar el formulario. HTMX hace POST multipart a /invoices/upload.
            await page.click("button[type=submit]")

            # 5. Esperar a que la fila aparezca con estado "listo".
            # El texto "listo" debe ser el valor localizado del badge de estado
            # (invoice.status.value en el template → "ready" en inglés o
            # el texto traducido si se añade i18n).
            # timeout=30_000 ms (30 s): tiempo suficiente para el pipeline completo
            # (upload a R2 + worker ARQ + llamada a Gemini + polling HTMX cada 2s).
            row = page.locator("tr").filter(has_text=FIXTURE_PDF.name).first
            await expect(row).to_contain_text("listo", timeout=30_000)
        finally:
            # Siempre cerrar el browser, incluso si el test falla, para liberar
            # recursos del sistema operativo (proceso Chromium, puertos, sockets).
            await browser.close()
