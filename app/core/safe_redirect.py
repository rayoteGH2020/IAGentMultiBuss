"""Validación de rutas de retorno tras login (evita open redirect)."""

from __future__ import annotations

from urllib.parse import urlsplit

_MAX_LEN = 2048
# Destinos que no tienen sentido tras reautenticar (o provocarían bucles).
_EXCLUDED_PREFIXES: tuple[str, ...] = (
    "/login",
    "/signup",
    "/logout",
    "/onboarding",
    "/auth/",
    "/api/",
    "/static/",
)


def safe_internal_path(value: str | None) -> str | None:
    """Devuelve ``path?query`` si es una ruta interna segura; si no, None.

    Rechaza URLs absolutas o de protocolo relativo (``//evil.com``), barras
    invertidas (algunos navegadores las tratan como ``/``), caracteres de
    control y rutas de auth/API que no son páginas de la app.
    """
    if not value or len(value) > _MAX_LEN:
        return None
    if any(ord(ch) < 0x20 or ch == "\\" for ch in value):
        return None
    if not value.startswith("/") or value.startswith("//"):
        return None
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return None
    path = parts.path or "/"
    if any(path == p.rstrip("/") or path.startswith(p) for p in _EXCLUDED_PREFIXES):
        return None
    return f"{path}?{parts.query}" if parts.query else path


def path_from_url(url: str | None) -> str | None:
    """Extrae ``path?query`` de una URL absoluta (p. ej. cabecera HX-Current-URL)."""
    if not url or len(url) > _MAX_LEN:
        return None
    parts = urlsplit(url)
    path = parts.path or "/"
    return safe_internal_path(f"{path}?{parts.query}" if parts.query else path)
