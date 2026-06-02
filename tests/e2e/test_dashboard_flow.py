"""E2E: flujo login Clerk → home → Mi cuenta → renombrar organización."""

import os

import pytest
from playwright.async_api import async_playwright

pytestmark = pytest.mark.e2e

BASE_URL = os.getenv("E2E_BASE_URL", "http://localhost:8000")
TEST_EMAIL = os.getenv("E2E_TEST_EMAIL")
TEST_PASSWORD = os.getenv("E2E_TEST_PASSWORD")


@pytest.mark.skipif(
    not TEST_EMAIL or not TEST_PASSWORD,
    reason="E2E_TEST_EMAIL/PASSWORD not configured",
)
@pytest.mark.asyncio
async def test_login_to_dashboard() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"{BASE_URL}/login")

        await page.fill("input[name='identifier']", TEST_EMAIL)
        await page.click("button[type='submit']")
        await page.fill("input[name='password']", TEST_PASSWORD)
        await page.click("button[type='submit']")

        await page.wait_for_url(f"{BASE_URL}/")
        heading = page.locator("h1").first
        assert (await heading.inner_text()) != ""

        await page.click("a[href='/settings/profile']")
        await page.wait_for_url(f"{BASE_URL}/settings/profile")

        await page.fill("input[name='name']", "Nuevo Nombre Test")
        await page.click("button[type='submit']")

        await page.wait_for_selector("text=Nombre actualizado")

        await browser.close()
