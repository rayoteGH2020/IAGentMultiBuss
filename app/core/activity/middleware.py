"""Middleware ASGI: una fila ``request`` por petición y ``request_id`` de correlación."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog
from starlette.datastructures import MutableHeaders

from app.core.activity.buffer import build_row, record
from app.core.activity.capture import error_row
from app.core.activity.context import ActivityScope, activity_scope

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
UNMATCHED_ROUTE = "<unmatched>"
# Ruido sin valor para depurar: estáticos, sondas y polling HTMX de estado.
_EXCLUDED_PREFIXES = ("/static", "/health", "/metrics")


def _is_polling_route(template: str) -> bool:
    # /jobs/{kind}/{id}/status: el polling cada 2 s de document_row y knowledge_row.
    return template.startswith("/jobs/") and template.endswith("/status")


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    template = getattr(route, "path", None)
    return template if isinstance(template, str) else UNMATCHED_ROUTE


def _is_htmx(scope: Scope) -> bool:
    return any(k == b"hx-request" and v == b"true" for k, v in scope.get("headers", []))


class ActivityMiddleware:
    """Envuelve toda la app (se registra la última en ``create_app``).

    El ``request_id`` es siempre nuevo: un ``X-Request-ID`` del cliente se ignora,
    porque se podría falsificar. Se devuelve en la respuesta para que un usuario
    pueda citarlo al reportar un error.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid4()
        activity = ActivityScope(source="api", request_id=request_id)
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, str(request_id))
            await send(message)

        started = time.perf_counter()
        with (
            activity_scope(activity),
            structlog.contextvars.bound_contextvars(request_id=str(request_id)),
        ):
            try:
                await self.app(scope, receive, send_with_request_id)
            except Exception as exc:
                record(error_row(exc, name=type(exc).__name__, scope=activity))
                raise
            finally:
                self._record_request(scope, activity, status_code, started)

    @staticmethod
    def _record_request(
        scope: Scope, activity: ActivityScope, status_code: int, started: float
    ) -> None:
        path = str(scope.get("path", ""))
        if path.startswith(_EXCLUDED_PREFIXES):
            return
        template = _route_template(scope)
        if _is_polling_route(template):
            return
        record(
            build_row(
                kind="request",
                name=template,
                scope=activity,
                method=str(scope.get("method", ""))[:8],
                status_code=status_code,
                duration_ms=int((time.perf_counter() - started) * 1000),
                htmx=_is_htmx(scope),
            )
        )
