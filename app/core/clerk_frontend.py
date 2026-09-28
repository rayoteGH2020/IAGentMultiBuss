"""URLs del frontend de Clerk (clerk-js en CDN)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from app.config import Settings

# Versión fijada de @clerk/clerk-js (no usar @latest en plantillas).
# Actualizar de forma controlada tras probar login/signup/sign-out.
DEFAULT_CLERK_JS_VERSION = "5.125.10"

_CLERK_JS_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][\w.-]+)?$")


def normalize_clerk_js_version(value: str) -> str:
    """Normaliza y valida una versión semver de clerk-js."""
    candidate = value.strip().lstrip("@")
    if not _CLERK_JS_VERSION_RE.fullmatch(candidate):
        raise ValueError("clerk_js_version must be a semver like 5.125.10")
    return candidate


def clerk_browser_script_url(frontend_host: str, version: str) -> str:
    """URL del bundle clerk.browser.js servido por el Frontend API de Clerk."""
    host = frontend_host.strip().removeprefix("https://").removeprefix("http://").strip("/")
    ver = normalize_clerk_js_version(version)
    return f"https://{host}/npm/@clerk/clerk-js@{ver}/dist/clerk.browser.js"


def clerk_browser_context(settings: Settings) -> dict[str, str]:
    """Variables de plantilla para cargar clerk-js (components/clerk_browser_script.html)."""
    frontend_host = urlparse(settings.clerk_jwks_url).netloc
    return {
        "clerk_pub_key": settings.clerk_publishable_key,
        "clerk_frontend_host": frontend_host,
        "clerk_js_script_url": clerk_browser_script_url(frontend_host, settings.clerk_js_version),
    }
