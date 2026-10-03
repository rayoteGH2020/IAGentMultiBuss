"""Buffer en memoria por proceso y volcado periódico a ``activity_log``.

No se usa Redis (D029): con ``noeviction`` y 512 MB, una avalancha de eventos
podría llenar la memoria que necesita la cola ARQ. Si el buffer se llena se
descartan las filas más antiguas; si el volcado falla se pierde ese lote. En
ningún caso se interrumpe la petición o el job.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog

from app.config import get_settings
from app.core.activity.context import ActivityScope, current_scope, process_source

ActivityRow = dict[str, Any]
Writer = Callable[[list[ActivityRow]], Awaitable[None]]

MAX_BUFFERED_ROWS = 20_000
FLUSH_INTERVAL_SECONDS = 2.0
FLUSH_EARLY_ROWS = 500
WRITE_BATCH_ROWS = 1_000
_POLL_SECONDS = 0.25

# Eventos de este módulo: el procesador de captura los ignora por el prefijo.
logger = structlog.get_logger(__name__)


class ActivityBuffer:
    """Cola acotada; ``deque.append``/``popleft`` son seguros entre hilos."""

    def __init__(self, maxlen: int = MAX_BUFFERED_ROWS) -> None:
        self._rows: deque[ActivityRow] = deque(maxlen=maxlen)
        self.dropped = 0

    def add(self, row: ActivityRow) -> None:
        if len(self._rows) == self._rows.maxlen:
            self.dropped += 1
        self._rows.append(row)

    def drain(self, limit: int) -> list[ActivityRow]:
        batch: list[ActivityRow] = []
        while self._rows and len(batch) < limit:
            batch.append(self._rows.popleft())
        return batch

    def __len__(self) -> int:
        return len(self._rows)


_buffer = ActivityBuffer()


def get_buffer() -> ActivityBuffer:
    return _buffer


def build_row(
    *,
    kind: str,
    name: str,
    scope: ActivityScope | None = None,
    **fields: Any,
) -> ActivityRow:
    """Fila con los identificadores del contexto en curso (o de ``scope``)."""
    active = scope or current_scope()
    row: ActivityRow = {
        "occurred_at": datetime.now(UTC),
        "kind": kind,
        "source": active.source if active else process_source(),
        "name": name[:120],
        "tenant_id": active.tenant_id if active else None,
        "user_id": active.user_id if active else None,
        "request_id": active.request_id if active else None,
        "job_id": active.job_id if active else None,
        "parent_request_id": active.parent_request_id if active else None,
    }
    row.update(fields)
    return row


def record(row: ActivityRow) -> None:
    """Encola una fila si el registro está activo (``ACTIVITY_LOG_ENABLED``)."""
    if get_settings().activity_log_enabled:
        _buffer.add(row)


class ActivityFlusher:
    """Tarea asyncio que vuelca el buffer cada ~2 s, o antes si se acumula."""

    def __init__(
        self,
        writer: Writer,
        *,
        buffer: ActivityBuffer | None = None,
        interval: float = FLUSH_INTERVAL_SECONDS,
    ) -> None:
        self._writer = writer
        # ``is not None``: un buffer vacío es falso (``__len__`` == 0).
        self._buffer = buffer if buffer is not None else _buffer
        self._interval = interval
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="activity-log-flusher")

    async def stop(self) -> None:
        """Para la tarea y hace un último volcado."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.flush()

    async def flush(self) -> int:
        """Vuelca todo lo acumulado en lotes. Devuelve las filas escritas."""
        written = 0
        while batch := self._buffer.drain(WRITE_BATCH_ROWS):
            try:
                await self._writer(batch)
            except Exception as exc:
                logger.warning(
                    "activity_log.flush_failed",
                    lost_count=len(batch),
                    error_type=type(exc).__name__,
                )
                continue
            written += len(batch)
        if self._buffer.dropped:
            logger.warning("activity_log.dropped", dropped_count=self._buffer.dropped)
            self._buffer.dropped = 0
        return written

    async def _run(self) -> None:
        last = time.monotonic()
        while True:
            await asyncio.sleep(_POLL_SECONDS)
            due = time.monotonic() - last >= self._interval
            if due or len(self._buffer) >= FLUSH_EARLY_ROWS:
                await self.flush()
                last = time.monotonic()
