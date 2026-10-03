"""Healthcheck del contenedor API (Docker HEALTHCHECK).

En producción la app aplica TrustedHostMiddleware (solo el dominio público) y
HTTPSRedirectMiddleware. Una petición HTTP pelada a 127.0.0.1 recibiría 400 o
307, así que se envía el Host público y X-Forwarded-Proto=https, igual que
hace Caddy. Solo stdlib: se ejecuta con el Python de la imagen.
"""

import os
import sys
import urllib.request
from urllib.parse import urlparse

HEALTH_URL = "http://127.0.0.1:8000/health"
TIMEOUT_SECONDS = 4.0


def build_headers(app_base_url: str) -> dict[str, str]:
    """Cabeceras que simulan una petición llegada a través de Caddy."""
    headers = {"X-Forwarded-Proto": "https"}
    host = urlparse(app_base_url).hostname
    if host:
        headers["Host"] = host
    return headers


def check(app_base_url: str) -> bool:
    """True si /health responde 200."""
    request = urllib.request.Request(HEALTH_URL, headers=build_headers(app_base_url))  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return bool(response.status == 200)
    except OSError:
        return False


if __name__ == "__main__":
    sys.exit(0 if check(os.environ.get("APP_BASE_URL", "")) else 1)
