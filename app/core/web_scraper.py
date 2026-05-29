"""Web scraper para ingesta de URLs en la base de conocimiento (Paso 21 A).

Flujo:
  1. Validar URL (scheme, blacklist, longitud).
  2. Comprobar robots.txt simplificado (si Disallow: / → abortar).
  3. Descargar HTML con httpx.
  4. Convertir a texto plano con html2text.
  5. Truncar a knowledge_url_max_size_bytes.
  6. Devolver ScrapedResult.

ScrapingError se lanza ante cualquier error descriptible (URL inválida,
HTTP 4xx/5xx, robots bloqueando). Excepciones de red inesperadas escalan
al caller para que el job las trate como fallos retryables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import html2text
import httpx
import structlog

from app.config import Settings, get_settings
from app.core.errors import ScrapingError

logger = structlog.get_logger(__name__)

_MAX_URL_LENGTH = 2048
_ROBOTS_TIMEOUT_S = 5.0


@dataclass(frozen=True, slots=True)
class ScrapedResult:
    text: str
    title: str | None
    final_url: str
    char_count: int


def _validate_url(url: str, settings: Settings) -> None:
    """Valida el esquema, la blacklist y la longitud de la URL."""
    if len(url) > _MAX_URL_LENGTH:
        raise ScrapingError(f"La URL supera los {_MAX_URL_LENGTH} caracteres.")

    parsed = urlparse(url)
    if parsed.scheme not in settings.knowledge_url_allowed_schemes:
        allowed = ", ".join(settings.knowledge_url_allowed_schemes)
        raise ScrapingError(f"Esquema '{parsed.scheme}' no permitido. Usa: {allowed}.")
    if not parsed.netloc:
        raise ScrapingError("La URL no contiene un dominio válido.")

    for blocked in settings.knowledge_url_blacklist:
        if blocked and blocked.lower() in parsed.netloc.lower():
            raise ScrapingError(f"El dominio '{parsed.netloc}' está en la lista negra.")


async def _is_blocked_by_robots(url: str, client: httpx.AsyncClient) -> bool:
    """Comprobación simplificada de robots.txt: True si Disallow: / para User-agent: *.

    Solo verifica la regla más restrictiva posible. Si robots.txt no existe
    o no se puede leer, se permite el acceso (principio de menor restricción).
    """
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = await client.get(robots_url, timeout=_ROBOTS_TIMEOUT_S, follow_redirects=True)
        if not resp.is_success:
            return False  # robots.txt no disponible → permitir
        in_user_agent_star = False
        for raw_line in resp.text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            lower = line.lower()
            if lower.startswith("user-agent:"):
                agent = line.split(":", 1)[1].strip()
                in_user_agent_star = agent == "*"
            elif lower.startswith("disallow:") and in_user_agent_star:
                path = line.split(":", 1)[1].strip()
                if path == "/":
                    return True  # Disallow: / para todos
    except Exception:
        logger.debug("web_scraper.robots_fetch_failed", url=url)
    return False


def _extract_title(html: str) -> str | None:
    """Extrae el contenido del <title> del HTML."""
    match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if match:
        return match.group(1).strip()[:200] or None
    return None


def _html_to_text(html: str) -> str:
    """Convierte HTML a texto plano ignorando links e imágenes."""
    converter = html2text.HTML2Text()
    converter.ignore_links = True
    converter.ignore_images = True
    converter.ignore_emphasis = True
    converter.body_width = 0  # sin wrap de línea
    result: str = converter.handle(html)
    return result.strip()


async def scrape_url(url: str, settings: Settings | None = None) -> ScrapedResult:
    """Descarga y convierte una URL pública a texto plano.

    Args:
        url: URL pública con esquema https (por defecto).
        settings: Instancia de Settings; si None se usa get_settings().

    Returns:
        ScrapedResult con el texto extraído y metadatos.

    Raises:
        ScrapingError: URL inválida, bloqueada por robots.txt o HTTP error.
    """
    s = settings or get_settings()
    _validate_url(url, s)

    timeout = httpx.Timeout(s.knowledge_url_timeout_s)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        if await _is_blocked_by_robots(url, client):
            raise ScrapingError(
                "El sitio web prohíbe el acceso automatizado (robots.txt Disallow: /)."
            )

        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ScrapingError(
                f"Error HTTP {exc.response.status_code} al acceder a la URL."
            ) from exc
        except httpx.RequestError as exc:
            raise ScrapingError(f"No se pudo conectar con la URL: {exc}") from exc

        final_url = str(resp.url)
        html = resp.text
        title = _extract_title(html)
        text = _html_to_text(html)

    # Truncar a max_size_bytes (calculado en bytes UTF-8)
    encoded = text.encode("utf-8")
    if len(encoded) > s.knowledge_url_max_size_bytes:
        text = encoded[: s.knowledge_url_max_size_bytes].decode("utf-8", errors="ignore")
        logger.info(
            "web_scraper.truncated",
            url=url,
            original_bytes=len(encoded),
            max_bytes=s.knowledge_url_max_size_bytes,
        )

    logger.info(
        "web_scraper.scraped",
        url=url,
        final_url=final_url,
        title=title,
        char_count=len(text),
    )
    return ScrapedResult(
        text=text,
        title=title,
        final_url=final_url,
        char_count=len(text),
    )
