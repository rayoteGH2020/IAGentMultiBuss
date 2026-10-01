"""Captura de logs como filas de activity_log, sin datos personales (D029)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import structlog
from app.core.activity.capture import app_frame_location, capture_activity, sanitize_data
from app.core.activity.context import ActivityScope, activity_scope

_PERSONAL = "Juan Pérez"


def _raise_value_error() -> None:
    raise ValueError(f"fallo con {_PERSONAL} juan@example.com")


def _logger() -> Any:
    """Logger con la misma cadena de captura que configure_logging (sin renderer)."""
    return structlog.wrap_logger(
        structlog.ReturnLogger(),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.CallsiteParameterAdder(
                [
                    structlog.processors.CallsiteParameter.PATHNAME,
                    structlog.processors.CallsiteParameter.LINENO,
                    structlog.processors.CallsiteParameter.FUNC_NAME,
                ]
            ),
            capture_activity,
        ],
    )


# ── sanitize_data ─────────────────────────────────────────────────────────────


def test_sanitize_keeps_ids_codes_and_metrics() -> None:
    doc_id = uuid4()
    data = sanitize_data(
        {
            "event": "x",
            "document_id": doc_id,
            "invoice_ids": [str(doc_id)],
            "error_code": "too_many_pages",
            "chunk_count": 12,
            "latency_ms": 85.5,
            "status": "failed",
            "mime_type": "application/pdf",
            "force": True,
        }
    )

    assert data == {
        "document_id": str(doc_id),
        "invoice_ids": [str(doc_id)],
        "error_code": "too_many_pages",
        "chunk_count": 12,
        "latency_ms": 85.5,
        "status": "failed",
        "mime_type": "application/pdf",
        "force": True,
    }


def test_sanitize_drops_personal_data_by_key_and_by_value() -> None:
    data = sanitize_data(
        {
            "event": "x",
            "email": "juan@example.com",
            "filename": "factura.pdf",
            "customer_ref": "abcd1234abcd1234",
            "reason": "texto libre",
            "status": "Juan Pérez",
            "error_code": "a@b.com",
            "tenant_id": str(uuid4()),
            "request_id": str(uuid4()),
            "path": "/documents/123",
            "invoice_ids": ["ok", "no valido"],
        }
    )

    assert data is None


# ── capture_activity ──────────────────────────────────────────────────────────


def test_event_row_has_level_location_and_safe_data(activity_rows: list[dict[str, Any]]) -> None:
    doc_id = uuid4()
    rendered = _logger().info("worker.invoice.done", invoice_id=doc_id, filename="x.pdf")

    (row,) = activity_rows
    assert row["kind"] == "event"
    assert row["name"] == "worker.invoice.done"
    assert row["level"] == "info"
    assert row["location"].endswith(":test_event_row_has_level_location_and_safe_data"), row[
        "location"
    ]
    assert "test_activity_capture.py:" in row["location"]
    assert row["data"] == {"invoice_id": str(doc_id)}
    # Los campos del callsite no llegan al log de consola.
    assert not {"pathname", "lineno", "func_name"} & set(rendered[1])


def test_logged_exception_becomes_error_row_without_message(
    activity_rows: list[dict[str, Any]],
) -> None:
    try:
        _raise_value_error()
    except ValueError:
        _logger().exception("worker.invoice.failed", invoice_id=str(uuid4()))

    (row,) = activity_rows
    assert row["kind"] == "error"
    assert row["level"] == "error"
    assert row["data"]["error_type"] == "ValueError"
    # Sin frame de app/ en la excepción: se usa el punto del log.
    assert "test_activity_capture.py:" in row["location"]
    assert _PERSONAL not in str(row)
    assert "example.com" not in str(row)


def test_debug_and_own_events_are_not_activity_rows(activity_rows: list[dict[str, Any]]) -> None:
    logger = _logger()
    logger.debug("jobs.invoice_status", invoice_id=str(uuid4()))
    logger.warning("activity_log.flush_failed", lost_count=3)

    assert activity_rows == []


def test_rows_carry_scope_identifiers(activity_rows: list[dict[str, Any]]) -> None:
    scope = ActivityScope(source="api", request_id=uuid4(), tenant_id=uuid4(), user_id=uuid4())
    with activity_scope(scope):
        _logger().info("chat.turn_completed", tenant_id="ignored-inside-scope")

    (row,) = activity_rows
    assert row["source"] == "api"
    assert (row["request_id"], row["tenant_id"], row["user_id"]) == (
        scope.request_id,
        scope.tenant_id,
        scope.user_id,
    )


def test_outside_scope_tenant_comes_from_event(activity_rows: list[dict[str, Any]]) -> None:
    tenant_id = uuid4()
    _logger().info("quota.monthly_exceeded", tenant_id=str(tenant_id), user_id="no-uuid")

    (row,) = activity_rows
    assert row["tenant_id"] == tenant_id
    assert row["user_id"] is None


def test_capture_never_raises(monkeypatch: Any, activity_rows: list[dict[str, Any]]) -> None:
    from app.core.activity import capture

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(capture, "_event_row", _boom)
    event = capture_activity(None, "info", {"event": "x", "pathname": "p", "lineno": 1})

    assert event == {"event": "x"}


def test_app_frame_location_points_to_app_code() -> None:
    from app.core.log_redaction import pseudonymize

    try:
        pseudonymize(object.__new__(_Unprintable))
    except RuntimeError as exc:
        location = app_frame_location(exc)
    assert location is not None
    assert location.startswith("app/core/log_redaction.py:")
    assert location.endswith(":pseudonymize")


class _Unprintable:
    def __str__(self) -> str:
        raise RuntimeError("no se puede convertir")
