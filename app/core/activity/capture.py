"""Captura de los logs de structlog como filas ``event`` / ``error`` (D029).

``data`` solo guarda lo que pasa dos filtros: la clave (lista fija o sufijo de
identificador, código o métrica) y el valor (número, booleano o texto corto sin
espacios ni ``@``). Así un ``reason="Juan Pérez"`` o un email añadidos mañana a
un log no llegan a la tabla. Los seudónimos ``*_ref`` tampoco: un dato
seudonimizado sigue siendo dato personal y para depurar no hace falta.
"""

from __future__ import annotations

import contextlib
import re
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.activity.buffer import build_row, record
from app.core.activity.context import ActivityScope, current_scope
from app.core.log_redaction import resolve_exception, short_path

if TYPE_CHECKING:
    from collections.abc import MutableMapping
    from types import TracebackType

_ALLOWED_KEYS = frozenset(
    {
        "action",
        "attempt_number",
        "authenticated",
        "chunk_count",
        "citations",
        "code",
        "confidence",
        "dense",
        "doc_type",
        "document_kind",
        "error_code",
        "error_type",
        "force",
        "from_kind",
        "job_try",
        "kind",
        "knowledge_tools",
        "latency_ms",
        "llm_calls",
        "merged",
        "method",
        "mime_type",
        "model",
        "needs_clarification",
        "needs_confirmation",
        "pages",
        "plan_code",
        "provider",
        "provider_billing",
        "provider_overload",
        "replace_existing",
        "returned",
        "role",
        "sparse",
        "status",
        "status_code",
        "task",
        "to_kind",
        "tools",
        "used",
        "cap",
        "channel",
        "limit",
        "period",
        "threshold",
    }
)
_ALLOWED_SUFFIXES = ("_id", "_ids", "_count", "_ms", "_bytes", "_code", "_type")
# Van a columnas propias o son metadatos de structlog.
_RESERVED_KEYS = frozenset(
    {
        "event",
        "level",
        "timestamp",
        "exc_info",
        "exception",
        "stack_info",
        "logger",
        "tenant_id",
        "user_id",
        "request_id",
        "job_id",
        "parent_request_id",
        "pathname",
        "lineno",
        "func_name",
    }
)
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9_.:/\-]{1,80}$")
_MAX_LIST_ITEMS = 20
_MAX_DATA_KEYS = 30
_SKIP_PREFIX = "activity_log."
_LEVEL_ALIASES = {"exception": "error", "warn": "warning", "critical": "error"}

_Scalar = str | int | float | bool | None


def _safe_scalar(value: Any) -> tuple[bool, _Scalar]:
    if value is None or isinstance(value, bool | int | float):
        return True, value
    if isinstance(value, UUID):
        return True, str(value)
    if isinstance(value, str) and _SAFE_TEXT.match(value):
        return True, value
    return False, None


def _safe_value(value: Any) -> tuple[bool, _Scalar | list[_Scalar]]:
    if isinstance(value, list | tuple | set | frozenset):
        items = list(value)[:_MAX_LIST_ITEMS]
        cleaned = [_safe_scalar(item) for item in items]
        if all(ok for ok, _ in cleaned):
            return True, [item for _, item in cleaned]
        return False, None
    return _safe_scalar(value)


def _key_allowed(key: str) -> bool:
    return key in _ALLOWED_KEYS or key.endswith(_ALLOWED_SUFFIXES)


def sanitize_data(event_dict: MutableMapping[str, Any]) -> dict[str, Any] | None:
    """Extras del evento que pasan la lista permitida por clave y por valor."""
    data: dict[str, Any] = {}
    for key, value in event_dict.items():
        if key in _RESERVED_KEYS or not _key_allowed(key):
            continue
        ok, cleaned = _safe_value(value)
        if ok:
            data[key] = cleaned
        if len(data) >= _MAX_DATA_KEYS:
            break
    return data or None


def app_frame_location(exc: BaseException) -> str | None:
    """``fichero:línea:función`` del frame más profundo dentro de ``app/``."""
    location: str | None = None
    tb: TracebackType | None = exc.__traceback__
    while tb is not None:
        path = short_path(tb.tb_frame.f_code.co_filename)
        if path.startswith("app/"):
            location = f"{path}:{tb.tb_lineno}:{tb.tb_frame.f_code.co_name}"
        tb = tb.tb_next
    return location


def error_row(
    exc: BaseException, *, name: str, scope: ActivityScope | None = None
) -> dict[str, Any]:
    """Fila ``error``: tipo y frame de ``app/``, nunca el mensaje de la excepción."""
    return build_row(
        kind="error",
        name=name,
        scope=scope,
        level="error",
        location=app_frame_location(exc),
        data={"error_type": type(exc).__name__},
    )


def _uuid_or_none(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


def _event_row(event_dict: MutableMapping[str, Any], method_name: str) -> dict[str, Any] | None:
    event = event_dict.get("event")
    if not isinstance(event, str) or event.startswith(_SKIP_PREFIX):
        return None
    level = str(event_dict.get("level") or method_name).lower()
    level = _LEVEL_ALIASES.get(level, level)
    if level == "debug":
        return None
    callsite = None
    if "pathname" in event_dict:
        pathname = short_path(str(event_dict["pathname"]))
        callsite = f"{pathname}:{event_dict.get('lineno')}:{event_dict.get('func_name')}"

    exc = resolve_exception(event_dict.get("exc_info"))
    data = sanitize_data(event_dict)
    if exc is not None:
        data = {**(data or {}), "error_type": type(exc).__name__}
    row = build_row(
        kind="error" if exc is not None else "event",
        name=event,
        level=level,
        location=(app_frame_location(exc) if exc is not None else None) or callsite,
        data=data,
    )
    # Logs fuera de petición o job (crons, arranque): el tenant del propio evento.
    if current_scope() is None:
        row["tenant_id"] = _uuid_or_none(event_dict.get("tenant_id"))
        row["user_id"] = _uuid_or_none(event_dict.get("user_id"))
    return row


def capture_activity(
    _logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Procesador structlog: encola el evento y quita los campos de ``CallsiteParameterAdder``.

    Nunca lanza: un fallo aquí no puede romper el logging de la app.
    """
    with contextlib.suppress(Exception):
        row = _event_row(event_dict, method_name)
        if row is not None:
            record(row)
    for key in ("pathname", "lineno", "func_name"):
        event_dict.pop(key, None)
    return event_dict
