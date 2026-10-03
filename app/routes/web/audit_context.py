"""Metadatos HTTP (IP, user agent) para entradas de ``audit_log``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.audit_service import AuditRequestContext

if TYPE_CHECKING:
    from fastapi import Request


def audit_request_context(request: Request) -> AuditRequestContext:
    """IP del cliente y user agent; único helper para todas las rutas que auditan.

    La IP es ``request.client.host``: uvicorn la toma de ``X-Forwarded-For`` solo
    si la conexión viene de ``--forwarded-allow-ips`` (Caddy, red interna). Leer la
    cabecera aquí permitiría a cualquiera falsificar la IP auditada (Backlog P2c-3).
    """
    client = request.client
    return AuditRequestContext(
        ip=client.host if client else None,
        user_agent=request.headers.get("user-agent"),
    )
