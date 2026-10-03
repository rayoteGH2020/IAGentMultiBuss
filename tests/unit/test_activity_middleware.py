"""Middleware de actividad: filas request, request_id y exclusiones (D029)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import structlog
from app.core.activity.context import current_scope, set_identity
from app.core.activity.middleware import REQUEST_ID_HEADER, ActivityMiddleware
from fastapi import FastAPI

_TENANT = uuid4()
_USER = uuid4()


def _app() -> FastAPI:
    app = FastAPI()
    log = structlog.get_logger("test")

    @app.get("/documents/{document_id}")
    async def detail(document_id: str) -> dict[str, Any]:
        set_identity(tenant_id=_TENANT, user_id=_USER)
        scope = current_scope()
        return {"request_id": str(scope.request_id) if scope else None}

    @app.get("/jobs/invoice/{invoice_id}/status")
    async def polling(invoice_id: str) -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/boom")
    async def boom() -> None:
        log.info("before.boom")
        raise KeyError("Juan Pérez")

    app.add_middleware(ActivityMiddleware)
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _requests(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r["kind"] == "request"]


async def test_request_row_uses_route_template_and_identity(
    activity_rows: list[dict[str, Any]],
) -> None:
    async with _client(_app()) as client:
        resp = await client.get(
            "/documents/abc-123",
            headers={"HX-Request": "true", REQUEST_ID_HEADER: "spoofed"},
        )

    request_id = UUID(resp.headers[REQUEST_ID_HEADER])
    assert resp.json()["request_id"] == str(request_id)
    (row,) = _requests(activity_rows)
    assert row["name"] == "/documents/{document_id}"
    assert (row["method"], row["status_code"], row["htmx"]) == ("GET", 200, True)
    assert row["request_id"] == request_id
    assert (row["tenant_id"], row["user_id"]) == (_TENANT, _USER)
    assert row["duration_ms"] >= 0
    assert "abc-123" not in str(row)


async def test_polling_health_and_static_are_not_recorded(
    activity_rows: list[dict[str, Any]],
) -> None:
    async with _client(_app()) as client:
        polled = await client.get("/jobs/invoice/1/status")
        await client.get("/health")
        await client.get("/static/css/app.css")

    assert REQUEST_ID_HEADER in polled.headers
    assert _requests(activity_rows) == []


async def test_unmatched_route_is_recorded_without_path(
    activity_rows: list[dict[str, Any]],
) -> None:
    async with _client(_app()) as client:
        await client.get("/wp-admin/secret-path")

    (row,) = _requests(activity_rows)
    assert (row["name"], row["status_code"]) == ("<unmatched>", 404)


async def test_unhandled_exception_records_error_and_500(
    activity_rows: list[dict[str, Any]],
) -> None:
    async with _client(_app()) as client:
        resp = await client.get("/boom")

    assert resp.status_code == 500
    errors = [r for r in activity_rows if r["kind"] == "error"]
    assert [e["data"]["error_type"] for e in errors] == ["KeyError"]
    (request_row,) = _requests(activity_rows)
    assert request_row["status_code"] == 500
    assert errors[0]["request_id"] == request_row["request_id"]
    assert "Juan" not in str(activity_rows)


@pytest.mark.parametrize("path", ["/documents/1"])
async def test_request_id_header_is_unique_per_request(
    path: str, activity_rows: list[dict[str, Any]]
) -> None:
    async with _client(_app()) as client:
        first = await client.get(path)
        second = await client.get(path)

    assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]
