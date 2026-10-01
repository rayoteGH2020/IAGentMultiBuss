"""Contexto de actividad de la petición o del job en curso."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Iterator
    from uuid import UUID

Source = Literal["api", "worker"]


@dataclass(slots=True)
class ActivityScope:
    """Identificadores que se copian en cada fila de ``activity_log``.

    Es mutable a propósito: ``AuthMiddleware`` (``BaseHTTPMiddleware``) corre la
    ruta en otra tarea, así que una contextvar fijada dentro no se vería fuera; el
    mismo objeto compartido sí.
    """

    source: Source
    request_id: UUID | None = None
    job_id: str | None = None
    parent_request_id: UUID | None = None
    tenant_id: UUID | None = None
    user_id: UUID | None = None


_current: ContextVar[ActivityScope | None] = ContextVar("activity_scope", default=None)
_process_source: Source = "api"


def set_process_source(source: Source) -> None:
    """Origen de las filas fuera de petición o job (``worker`` al arrancar ARQ)."""
    global _process_source
    _process_source = source


def process_source() -> Source:
    return _process_source


def current_scope() -> ActivityScope | None:
    return _current.get()


@contextmanager
def activity_scope(scope: ActivityScope) -> Iterator[ActivityScope]:
    """Activa ``scope`` mientras dura el bloque."""
    token = _current.set(scope)
    try:
        yield scope
    finally:
        _current.reset(token)


def set_identity(*, tenant_id: UUID | None, user_id: UUID | None) -> None:
    """Anota tenant y usuario resueltos por la autenticación en la petición en curso."""
    scope = _current.get()
    if scope is None:
        return
    scope.tenant_id = tenant_id
    scope.user_id = user_id


def job_parent_kwargs() -> dict[str, Any]:
    """Kwargs para ``enqueue_job``: enlaza el job con la petición que lo lanza.

    Desde un job se propaga su propia petición de origen, de modo que toda la
    cadena cuelga de la misma petición.
    """
    scope = _current.get()
    if scope is None:
        return {}
    parent = scope.request_id or scope.parent_request_id
    return {"parent_request_id": str(parent)} if parent else {}
