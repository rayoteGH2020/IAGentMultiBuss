"""Security response headers (CSP compatible with Clerk browser SDK)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import Request, Response
    from starlette.types import ASGIApp

HSTS_HEADER_VALUE = "max-age=31536000; includeSubDomains"

# Orígenes Clerk genéricos (dev + prod + fraud/captcha). El FAPI concreto
# del tenant se añade aparte vía clerk_frontend_host.
_CLERK_SCRIPT_ORIGINS = (
    "https://*.clerk.accounts.dev",
    "https://*.clerk.com",
    "https://challenges.cloudflare.com",
    "https://*.protect.clerk.com",
)
_CLERK_CONNECT_ORIGINS = (
    "https://*.clerk.accounts.dev",
    "https://*.clerk.com",
    "https://clerk-telemetry.com",
    "https://*.clerk-telemetry.com",
    "https://*.protect.clerk.com",
)
_CLERK_FRAME_ORIGINS = (
    "https://challenges.cloudflare.com",
    "https://*.clerk.accounts.dev",
    "https://*.clerk.com",
    "https://*.protect.clerk.com",
)


def normalize_clerk_frontend_host(value: str | None) -> str | None:
    """Extrae hostname del Frontend API de Clerk (JWKS URL o host puro)."""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    if "://" not in raw:
        candidate = raw.split("/")[0].strip()
        return candidate or None
    return urlparse(raw).hostname


def build_content_security_policy(
    *,
    clerk_frontend_host: str | None = None,
    upgrade_insecure_requests: bool = False,
) -> str:
    """CSP con allowlist mínima para Clerk SignIn/SignUp embebidos.

    ``upgrade_insecure_requests`` solo en HTTPS/producción: en
    ``http://localhost`` rompería CSS/JS al forzar https://localhost.
    """
    host = normalize_clerk_frontend_host(clerk_frontend_host)
    fapi = f"https://{host}" if host else None

    # Alpine 3 (build estándar) evalúa atributos con `new Function`, que CSP
    # clasifica como 'unsafe-eval'. Sin esto, dropdowns/x-data mueren en silencio
    # con "Alpine Expression Error". Alternativa futura: @alpinejs/csp + revisar
    # expresiones (sin arrow fn / window.* / property assign anidados).
    script_src = [
        "'self'",
        "'unsafe-inline'",
        "'unsafe-eval'",
        *_CLERK_SCRIPT_ORIGINS,
    ]
    connect_src = ["'self'", *_CLERK_CONNECT_ORIGINS]
    frame_src = ["'self'", *_CLERK_FRAME_ORIGINS]
    if fapi:
        if fapi not in script_src:
            script_src.append(fapi)
        if fapi not in connect_src:
            connect_src.append(fapi)
        if fapi not in frame_src:
            frame_src.append(fapi)

    directives = [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        f"script-src {' '.join(script_src)}",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob: https:",
        "font-src 'self' data:",
        f"connect-src {' '.join(connect_src)}",
        f"frame-src {' '.join(frame_src)}",
        "worker-src 'self' blob:",
    ]
    if upgrade_insecure_requests:
        directives.append("upgrade-insecure-requests")
    return "; ".join(directives)


def build_security_headers(
    *,
    clerk_frontend_host: str | None = None,
    upgrade_insecure_requests: bool = False,
) -> dict[str, str]:
    return {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": (
            "camera=(), microphone=(self), geolocation=(), payment=(), usb=(), "
            "accelerometer=(), gyroscope=(), magnetometer=()"
        ),
        "Content-Security-Policy": build_content_security_policy(
            clerk_frontend_host=clerk_frontend_host,
            upgrade_insecure_requests=upgrade_insecure_requests,
        ),
    }


# Compat: tests / imports que esperan un dict estático base.
SECURITY_HEADERS: dict[str, str] = build_security_headers()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        hsts_enabled: bool = False,
        clerk_frontend_host: str | None = None,
        upgrade_insecure_requests: bool = False,
    ) -> None:
        super().__init__(app)
        self._hsts_enabled = hsts_enabled
        self._headers = build_security_headers(
            clerk_frontend_host=clerk_frontend_host,
            upgrade_insecure_requests=upgrade_insecure_requests,
        )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        for header, value in self._headers.items():
            response.headers.setdefault(header, value)
        if self._hsts_enabled:
            response.headers.setdefault("Strict-Transport-Security", HSTS_HEADER_VALUE)
        return response
