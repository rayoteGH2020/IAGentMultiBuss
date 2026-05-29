"""Tests unitarios del scraper web (Paso 21 A.6)."""

from __future__ import annotations

import pytest
import respx
from app.config import get_settings
from app.core.errors import ScrapingError
from app.core.web_scraper import ScrapedResult, scrape_url
from httpx import Response

# ---------------------------------------------------------------------------
# _validate_url — validaciones puras vía scrape_url con URL inválida
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrape_url_rejects_http_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    """URL con esquema http (no https) → ScrapingError."""
    monkeypatch.setenv("KNOWLEDGE_URL_ALLOWED_SCHEMES", '["https"]')
    get_settings.cache_clear()
    with pytest.raises(ScrapingError, match="Esquema"):
        await scrape_url("http://example.com/page")
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_scrape_url_rejects_blacklisted_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dominio en la blacklist → ScrapingError."""
    monkeypatch.setenv("KNOWLEDGE_URL_BLACKLIST", '["blocked.com"]')
    get_settings.cache_clear()
    with pytest.raises(ScrapingError, match="lista negra"):
        await scrape_url("https://blocked.com/page")
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_scrape_url_rejects_too_long_url() -> None:
    """URL que supera 2048 chars → ScrapingError."""
    long_url = "https://example.com/" + "x" * 2100
    with pytest.raises(ScrapingError, match="2048"):
        await scrape_url(long_url)


# ---------------------------------------------------------------------------
# robots.txt — bloqueo simplificado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrape_url_blocked_by_robots() -> None:
    """robots.txt con Disallow: / para User-agent: * → ScrapingError."""
    robots_body = "User-agent: *\nDisallow: /\n"
    with respx.mock:
        respx.get("https://example.com/robots.txt").mock(
            return_value=Response(200, text=robots_body)
        )
        with pytest.raises(ScrapingError, match=r"robots\.txt"):
            await scrape_url("https://example.com/page")


@pytest.mark.asyncio
async def test_scrape_url_allowed_when_robots_not_found() -> None:
    """robots.txt con 404 → se permite el acceso (principio de menor restricción)."""
    html = "<html><head><title>Página</title></head><body>Contenido</body></html>"
    with respx.mock:
        respx.get("https://example.com/robots.txt").mock(return_value=Response(404))
        respx.get("https://example.com/page").mock(return_value=Response(200, text=html))
        result = await scrape_url("https://example.com/page")
    assert isinstance(result, ScrapedResult)
    assert "Contenido" in result.text


# ---------------------------------------------------------------------------
# Scraping exitoso — extracción de texto y título
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrape_url_extracts_title_and_text() -> None:
    """El scraper extrae el título del <title> y convierte el body a texto plano."""
    html = (
        "<html>"
        "<head><title>Preguntas Frecuentes</title></head>"
        "<body><h1>FAQ</h1><p>Abrimos de lunes a viernes.</p></body>"
        "</html>"
    )
    with respx.mock:
        respx.get("https://example.com/robots.txt").mock(return_value=Response(404))
        respx.get("https://example.com/faq").mock(return_value=Response(200, text=html))
        result = await scrape_url("https://example.com/faq")

    assert result.title == "Preguntas Frecuentes"
    assert "Abrimos de lunes a viernes" in result.text
    assert result.char_count == len(result.text)
    assert result.final_url == "https://example.com/faq"


@pytest.mark.asyncio
async def test_scrape_url_truncates_large_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contenido mayor que knowledge_url_max_size_bytes se trunca."""
    monkeypatch.setenv("KNOWLEDGE_URL_MAX_SIZE_BYTES", "100")
    get_settings.cache_clear()

    big_text = "A" * 10000
    html = f"<html><body><p>{big_text}</p></body></html>"

    with respx.mock:
        respx.get("https://example.com/robots.txt").mock(return_value=Response(404))
        respx.get("https://example.com/big").mock(return_value=Response(200, text=html))
        result = await scrape_url("https://example.com/big")

    max_bytes = get_settings().knowledge_url_max_size_bytes
    assert len(result.text.encode("utf-8")) <= max_bytes
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Errores HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrape_url_raises_on_http_404() -> None:
    """HTTP 404 de la URL objetivo → ScrapingError con el código."""
    with respx.mock:
        respx.get("https://example.com/robots.txt").mock(return_value=Response(404))
        respx.get("https://example.com/notfound").mock(return_value=Response(404))
        with pytest.raises(ScrapingError, match="404"):
            await scrape_url("https://example.com/notfound")


@pytest.mark.asyncio
async def test_scrape_url_raises_on_http_500() -> None:
    """HTTP 500 de la URL objetivo → ScrapingError."""
    with respx.mock:
        respx.get("https://example.com/robots.txt").mock(return_value=Response(404))
        respx.get("https://example.com/error").mock(return_value=Response(500))
        with pytest.raises(ScrapingError, match="500"):
            await scrape_url("https://example.com/error")
