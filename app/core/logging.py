import logging
import sys
from typing import TYPE_CHECKING, cast

import structlog

from app.config import get_settings
from app.core.activity.capture import capture_activity
from app.core.log_redaction import RedactLogRecordFilter, redact_exc_info

if TYPE_CHECKING:
    from structlog.types import Processor

# Loggers de librerías con handlers propios (uvicorn los crea antes de cargar la
# app; arq al arrancar el worker). El filtro va en el handler: los filtros de
# logger no ven los registros que llegan propagados de loggers hijos.
_LIBRARY_LOGGERS = ("", "uvicorn", "uvicorn.access", "arq")


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level)
    # Fuera de desarrollo, los tracebacks salen sin mensaje ni variables locales:
    # el texto de una excepción puede llevar contenido del cliente (Backlog P2c-2).
    redact_tracebacks = not settings.is_dev

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]
    if settings.activity_log_enabled:
        # Cada log INFO+ también va a activity_log (D029). Antes de la redacción de
        # tracebacks: la captura necesita la excepción para su tipo y frame de app/
        # (nunca el mensaje). capture_activity quita los campos de callsite.
        shared_processors += [
            structlog.processors.CallsiteParameterAdder(
                [
                    structlog.processors.CallsiteParameter.PATHNAME,
                    structlog.processors.CallsiteParameter.LINENO,
                    structlog.processors.CallsiteParameter.FUNC_NAME,
                ]
            ),
            capture_activity,
        ]

    if settings.is_dev:
        renderer: Processor = structlog.dev.ConsoleRenderer(colors=True)
        exc_processor: Processor = structlog.processors.format_exc_info
    else:
        renderer = structlog.processors.JSONRenderer()
        exc_processor = redact_exc_info

    structlog.configure(
        processors=[
            *shared_processors,
            exc_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Redirigir logs estándar a structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )
    _install_record_filter(redact_tracebacks=redact_tracebacks)

    # Silenciar logs verbosos
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def configure_worker_logging() -> None:
    """Logging del worker ARQ (``on_startup``): misma configuración que la API.

    Sin esto el worker usaría la configuración por defecto de structlog, que con
    ``rich`` instalado vuelca las variables locales en cada traceback.
    """
    configure_logging()
    arq_logger = logging.getLogger("arq")
    if arq_logger.handlers:
        # arq ya tiene su handler (dictConfig del CLI): sin esto, cada línea saldría
        # dos veces, también por el handler raíz de basicConfig.
        arq_logger.propagate = False


def _install_record_filter(*, redact_tracebacks: bool) -> None:
    for name in _LIBRARY_LOGGERS:
        for handler in logging.getLogger(name).handlers:
            existing = [f for f in handler.filters if isinstance(f, RedactLogRecordFilter)]
            for old in existing:
                handler.removeFilter(old)
            handler.addFilter(RedactLogRecordFilter(redact_tracebacks=redact_tracebacks))


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
