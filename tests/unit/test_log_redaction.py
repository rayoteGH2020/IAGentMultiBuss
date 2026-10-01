"""Redacción centralizada de logs (Backlog P2c-2)."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import arq.worker
import pytest
import structlog
from app.core import logging as app_logging
from app.core.log_redaction import (
    RedactLogRecordFilter,
    exception_summary,
    pseudonymize,
    redact_exc_info,
)

_PERSONAL_TEXT = "Juan Pérez 600123123"


def _raise_nested() -> None:
    try:
        raise KeyError(_PERSONAL_TEXT)
    except KeyError as exc:
        raise ValueError(f"fallo con {_PERSONAL_TEXT}") from exc


def _caught() -> BaseException:
    try:
        _raise_nested()
    except ValueError as exc:
        return exc
    raise AssertionError("unreachable")


# ── pseudonymize ─────────────────────────────────────────────────────────────


def test_pseudonymize_is_stable_short_and_hides_value() -> None:
    first = pseudonymize("+34600123123")

    assert first == pseudonymize("+34600123123")
    assert first is not None and len(first) == 16
    assert "600123123" not in first
    assert first != pseudonymize("+34600123124")


def test_pseudonymize_accepts_ints_and_ignores_empty() -> None:
    assert pseudonymize(123456) == pseudonymize("123456")
    assert pseudonymize(None) is None
    assert pseudonymize("   ") is None


# ── exception_summary / structlog ────────────────────────────────────────────


def test_exception_summary_has_types_and_frames_without_messages() -> None:
    summary = exception_summary(_caught())

    assert _PERSONAL_TEXT not in summary
    assert "fallo con" not in summary
    assert summary.splitlines()[0] == "ValueError"
    assert "caused by KeyError" in summary
    assert "_raise_nested" in summary
    assert "test_log_redaction.py:" in summary


def test_redact_exc_info_processor_replaces_traceback() -> None:
    try:
        _raise_nested()
    except ValueError:
        event = redact_exc_info(None, "error", {"event": "x", "exc_info": True})

    assert "exc_info" not in event
    assert event["exception"].startswith("ValueError")
    assert _PERSONAL_TEXT not in event["exception"]


def test_redact_exc_info_accepts_exception_instances_and_ignores_false() -> None:
    event = redact_exc_info(None, "error", {"event": "x", "exc_info": _caught()})
    assert event["exception"].startswith("ValueError")

    untouched = redact_exc_info(None, "info", {"event": "x", "exc_info": False})
    assert untouched == {"event": "x"}


# ── RedactLogRecordFilter (uvicorn, arq) ─────────────────────────────────────


def _record(name: str, msg: str, args: tuple[Any, ...], exc: BaseException | None = None) -> Any:
    exc_info = (type(exc), exc, exc.__traceback__) if exc else None
    return logging.LogRecord(name, logging.INFO, __file__, 1, msg, args, exc_info)


def test_arq_format_strings_match_installed_arq() -> None:
    """Si arq cambia sus mensajes, el filtro dejaría de redactar: falla aquí."""
    source = inspect.getsource(arq.worker)

    assert "'%6.2fs → %s(%s)%s'" in source
    assert "'%6.2fs ! %s failed, %s: %s'" in source


def test_filter_drops_arq_job_arguments() -> None:
    record = _record(
        "arq.worker",
        "%6.2fs → %s(%s)%s",
        (0.1, "abc:process_channel_message", f"'t', 'whatsapp', '{_PERSONAL_TEXT}'", ""),
    )

    RedactLogRecordFilter(redact_tracebacks=False).filter(record)

    assert _PERSONAL_TEXT not in record.getMessage()
    assert "process_channel_message" in record.getMessage()


def test_filter_drops_arq_failure_text_and_traceback_message() -> None:
    exc = _caught()
    record = _record(
        "arq.worker",
        "%6.2fs ! %s failed, %s: %s",
        (0.1, "abc:process_invoice", "ValueError", exc),
        exc,
    )
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))

    RedactLogRecordFilter(redact_tracebacks=True).filter(record)
    rendered = handler.format(record)

    assert _PERSONAL_TEXT not in rendered
    assert "fallo con" not in rendered
    assert "ValueError" in rendered
    assert "_raise_nested" in rendered


def test_filter_keeps_tracebacks_when_not_redacting() -> None:
    exc = _caught()
    record = _record("uvicorn.error", "Exception in ASGI application", (), exc)

    RedactLogRecordFilter(redact_tracebacks=False).filter(record)

    assert record.exc_info is not None


# ── configure_logging / configure_worker_logging ─────────────────────────────


@pytest.fixture
def restore_logging() -> Iterator[None]:
    saved_config = structlog.get_config()
    root = logging.getLogger()
    saved_root_handlers = list(root.handlers)
    arq_logger = logging.getLogger("arq")
    saved_arq = (list(arq_logger.handlers), arq_logger.propagate)
    yield
    structlog.configure(**saved_config)
    for handler in root.handlers:
        if handler not in saved_root_handlers:
            root.removeHandler(handler)
    for logger in (root, arq_logger, logging.getLogger("uvicorn")):
        for handler in logger.handlers:
            for flt in [f for f in handler.filters if isinstance(f, RedactLogRecordFilter)]:
                handler.removeFilter(flt)
    arq_logger.handlers, arq_logger.propagate = saved_arq


def _fake_settings(*, is_dev: bool) -> SimpleNamespace:
    return SimpleNamespace(is_dev=is_dev, log_level="INFO", activity_log_enabled=False)


def test_configure_logging_redacts_outside_development(
    restore_logging: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_logging, "get_settings", lambda: _fake_settings(is_dev=False))
    arq_handler = logging.StreamHandler()
    logging.getLogger("arq").addHandler(arq_handler)

    app_logging.configure_worker_logging()

    processors = structlog.get_config()["processors"]
    assert redact_exc_info in processors
    assert structlog.processors.format_exc_info not in processors
    filters = [f for f in arq_handler.filters if isinstance(f, RedactLogRecordFilter)]
    assert len(filters) == 1 and filters[0].redact_tracebacks is True
    root_filters = [
        f
        for h in logging.getLogger().handlers
        for f in h.filters
        if isinstance(f, RedactLogRecordFilter)
    ]
    assert root_filters
    assert logging.getLogger("arq").propagate is False


def test_configure_logging_keeps_full_tracebacks_in_development(
    restore_logging: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_logging, "get_settings", lambda: _fake_settings(is_dev=True))

    app_logging.configure_logging()
    app_logging.configure_logging()  # idempotente: no apila filtros

    assert structlog.processors.format_exc_info in structlog.get_config()["processors"]
    for handler in logging.getLogger().handlers:
        filters = [f for f in handler.filters if isinstance(f, RedactLogRecordFilter)]
        assert len(filters) == 1
        assert filters[0].redact_tracebacks is False


def test_worker_settings_configure_logging_on_startup() -> None:
    from app.jobs import settings as worker_settings

    assert worker_settings.WorkerSettings.on_startup is worker_settings.startup
