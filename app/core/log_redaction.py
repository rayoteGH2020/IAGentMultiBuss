"""Logs sin datos personales ni contenido de cliente (Seguridad_V2, Backlog P2c-2).

Norma para todo ``logger.*`` de la app:

- Identificadores de personas (teléfono, ``chat_id``, email): ``pseudonymize()``.
  Permite correlacionar líneas del mismo cliente sin exponer el dato.
- Excepciones de terceros (SDK, BD, Redis, SMTP, parsers): ``error_type=type(exc).__name__``,
  nunca ``str(exc)``; su texto puede llevar contenido del documento o del cliente.
  Los mensajes de ``AppError`` los escribe la app y sí se pueden registrar.
- Nombres de fichero, importes, comercios, proveedores o nombres extraídos: no se registran.

Los tracebacks se redactan aquí de forma centralizada fuera de desarrollo:
tipo de la excepción y frames ``fichero:línea:función``, sin mensaje ni variables.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config import get_settings

if TYPE_CHECKING:
    from collections.abc import MutableMapping
    from types import TracebackType

_PSEUDONYM_CONTEXT = b"log-pseudonym:v1:"
_PSEUDONYM_CHARS = 16
_MAX_FRAMES = 12
_MAX_CHAIN = 3

# Mensajes de arq.worker (arq 0.26) que llevan argumentos o texto de la excepción.
_ARQ_JOB_START_MSG = "%6.2fs → %s(%s)%s"
_ARQ_JOB_FAILED_MSG = "%6.2fs ! %s failed, %s: %s"
_REDACTED = "<redacted>"


def pseudonymize(value: object) -> str | None:
    """Seudónimo estable (HMAC-SHA256 con ``APP_SECRET_KEY``) para logs.

    Args:
        value: identificador de persona (teléfono, ``chat_id``, email...).

    Returns:
        16 caracteres hex, o ``None`` si ``value`` es ``None`` o vacío.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    key = get_settings().app_secret_key.get_secret_value().encode("utf-8")
    digest = hmac.new(key, _PSEUDONYM_CONTEXT + raw.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:_PSEUDONYM_CHARS]


def _short_path(filename: str) -> str:
    parts = Path(filename).parts
    for anchor in ("app", "site-packages"):
        if anchor in parts:
            index = len(parts) - 1 - parts[::-1].index(anchor)
            return "/".join(parts[index:])
    return Path(filename).name


def _frames(tb: TracebackType | None) -> list[str]:
    frames = [
        f"{_short_path(frame.filename)}:{frame.lineno}:{frame.name}"
        for frame in traceback.extract_tb(tb)
    ]
    return frames[-_MAX_FRAMES:]


def exception_summary(exc: BaseException) -> str:
    """Tipo y frames de la excepción (y de sus causas), sin mensajes ni variables."""
    lines: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(seen) < _MAX_CHAIN:
        seen.add(id(current))
        prefix = "" if not lines else "caused by "
        lines.append(f"{prefix}{type(current).__qualname__}")
        lines.extend(f"  at {frame}" for frame in _frames(current.__traceback__))
        current = current.__cause__ or current.__context__
    return "\n".join(lines)


def _resolve_exception(value: Any) -> BaseException | None:
    """Excepción de ``exc_info`` en cualquiera de sus formas (instancia, tupla, True)."""
    if isinstance(value, BaseException):
        return value
    if isinstance(value, tuple) and len(value) == 3 and isinstance(value[1], BaseException):
        return value[1]
    if value is True:
        return sys.exc_info()[1]
    return None


def redact_exc_info(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Procesador structlog: sustituye ``exc_info`` por ``exception_summary``."""
    exc = _resolve_exception(event_dict.pop("exc_info", None))
    if exc is not None:
        event_dict["exception"] = exception_summary(exc)
    return event_dict


class RedactLogRecordFilter(logging.Filter):
    """Filtro de handler para logs de librerías (uvicorn, arq) con la misma norma.

    - Argumentos de jobs ARQ: siempre fuera (``process_channel_message`` recibe
      el teléfono o ``chat_id`` y el texto del cliente final).
    - Texto de la excepción en el aviso de job fallido de ARQ: fuera.
    - Tracebacks: resumen sin mensaje si ``redact_tracebacks``.
    """

    def __init__(self, *, redact_tracebacks: bool) -> None:
        super().__init__()
        self.redact_tracebacks = redact_tracebacks

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith("arq") and isinstance(record.args, tuple):
            args = list(record.args)
            if record.msg == _ARQ_JOB_START_MSG and len(args) == 4:
                args[2] = _REDACTED
            elif record.msg == _ARQ_JOB_FAILED_MSG and len(args) == 4:
                args[3] = _REDACTED
            record.args = tuple(args)
        if self.redact_tracebacks and record.exc_info and record.exc_info[1] is not None:
            record.exc_text = exception_summary(record.exc_info[1])
            record.exc_info = None
        return True
