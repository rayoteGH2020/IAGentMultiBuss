"""E2E (Playwright): subir una factura y ver fila final 'listo' (gated por RUN_E2E)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, expect

pytestmark = pytest.mark.e2e

BASE_URL = os.getenv("E2E_BASE_URL", "http://localhost:8000")
AUTH_STATE = Path(os.getenv("E2E_AUTH_STATE", "tests/e2e/auth_state.json"))
FIXTURE_PDF = Path(__file__).resolve().parents[1] / "fixtures" / "invoices" / "ejemplo_01.pdf"


@pytest.mark.skipif(not os.getenv("RUN_E2E"), reason="set RUN_E2E=1 to enable")
@pytest.mark.asyncio
async def test_upload_invoice_and_see_result() -> None:
    if not AUTH_STATE.exists():
        pytest.skip(f"Falta {AUTH_STATE}; genera la sesión de Clerk con Playwright en dev.")
    if not FIXTURE_PDF.exists():
        pytest.skip(f"Falta fixture {FIXTURE_PDF}")

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            ctx = await browser.new_context(storage_state=str(AUTH_STATE))
            page = await ctx.new_page()

            await page.goto(f"{BASE_URL}/invoices")
            await expect(page.locator("h1")).to_contain_text("Facturas")

            await page.click("text=Subir facturas")
            async with page.expect_file_chooser() as fc_info:
                await page.click("label.border-dashed")
            file_chooser = await fc_info.value
            await file_chooser.set_files(str(FIXTURE_PDF))

            await page.click("button[type=submit]")

            row = page.locator("tr").filter(has_text=FIXTURE_PDF.name).first
            await expect(row).to_contain_text("listo", timeout=30_000)
        finally:
            await browser.close()
