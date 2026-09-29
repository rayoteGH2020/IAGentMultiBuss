"""Metadatos HTTP (IP, user agent) para entradas de ``audit_log``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.audit_service import AuditRequestContext

if TYPE_CHECKING:
    from fastapi import Request


def audit_request_context(request: Request) -> AuditRequestContext:
    """IP del cliente (primer salto de X-Forwarded-For si hay proxy) y user agent."""
    forwarded = request.headers.get("x-forwarded-for")
    ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else None)
    )
    return AuditRequestContext(ip=ip, user_agent=request.headers.get("user-agent"))
