"""Wrapper de jobs ARQ: contexto de actividad y una fila ``job`` por ejecución."""

from __future__ import annotations

import asyncio
import functools
import inspect
import time
from collections.abc import Callable, Coroutine
from typing import Any, cast
from uuid import UUID

import structlog
from arq.worker import Retry

from app.core.activity.buffer import build_row, record
from app.core.activity.capture import error_row
from app.core.activity.context import ActivityScope, activity_scope

type JobFunc = Callable[..., Coroutine[Any, Any, Any]]
_SAFE_OUTCOME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz_")


def _as_uuid(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


def _outcome(result: Any) -> str:
    """``status`` del dict que devuelve el job (``failed``, ``rejected``...) o ``ok``.

    Los jobs de extracción no relanzan: devuelven ``{"status": "failed"}`` para que
    ARQ no reintente, así que el resultado es lo que dice si fue bien.
    """
    if isinstance(result, dict):
        status = result.get("status")
        if isinstance(status, str) and status and set(status) <= _SAFE_OUTCOME_CHARS:
            return status[:32]
    return "ok"


def tracked_job[F: JobFunc](func: F) -> F:
    """Envuelve un job registrado en ``WorkerSettings``.

    - Quita el kwarg ``parent_request_id`` que añade ``job_parent_kwargs`` al encolar.
    - Toma ``tenant_id`` de los argumentos del job (todos los jobs con tenant lo
      llaman así), sin tocar cada job.
    - Mantiene ``__qualname__`` (``functools.wraps``): ARQ enruta por ese nombre.
    """
    signature = inspect.signature(func)

    @functools.wraps(func)
    async def wrapper(
        ctx: dict[str, Any], *args: Any, parent_request_id: str | None = None, **kwargs: Any
    ) -> Any:
        try:
            bound: dict[str, Any] = dict(signature.bind_partial(ctx, *args, **kwargs).arguments)
        except TypeError:
            bound = {}
        job_id = ctx.get("job_id")
        activity = ActivityScope(
            source="worker",
            job_id=str(job_id)[:128] if job_id else None,
            parent_request_id=_as_uuid(parent_request_id),
            tenant_id=_as_uuid(bound.get("tenant_id")),
        )
        outcome = "ok"
        started = time.perf_counter()
        with (
            activity_scope(activity),
            structlog.contextvars.bound_contextvars(job_id=activity.job_id),
        ):
            try:
                result = await func(ctx, *args, **kwargs)
            except Retry:
                outcome = "retry"
                raise
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            except Exception as exc:
                outcome = "error"
                record(error_row(exc, name=func.__qualname__, scope=activity))
                raise
            else:
                outcome = _outcome(result)
                return result
            finally:
                job_try = ctx.get("job_try")
                record(
                    build_row(
                        kind="job",
                        name=func.__qualname__,
                        scope=activity,
                        attempt=job_try if isinstance(job_try, int) else None,
                        outcome=outcome,
                        duration_ms=int((time.perf_counter() - started) * 1000),
                    )
                )

    # Mismo tipo que el job: ARQ acepta kwargs extra y cron() exige __qualname__.
    return cast("F", wrapper)
