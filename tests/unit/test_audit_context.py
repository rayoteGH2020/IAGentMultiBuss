"""IP de auditoría solo desde request.client.host (Backlog P2c-3)."""

from __future__ import annotations

from pathlib import Path

from app.routes.web.audit_context import audit_request_context
from starlette.requests import Request

_ROUTES_DIR = Path(__file__).resolve().parents[2] / "app" / "routes"


def _request(headers: dict[str, str], client: tuple[str, int] | None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": client,
    }
    return Request(scope)


def test_ignores_spoofed_x_forwarded_for() -> None:
    request = _request(
        {"X-Forwarded-For": "1.2.3.4, 10.0.0.1", "User-Agent": "pytest"},
        ("172.30.0.5", 51000),
    )

    ctx = audit_request_context(request)

    assert ctx.ip == "172.30.0.5"
    assert ctx.user_agent == "pytest"


def test_without_client_ip_is_none() -> None:
    assert audit_request_context(_request({}, None)).ip is None


def test_routes_do_not_parse_forwarded_headers_or_duplicate_helper() -> None:
    offenders = []
    for path in _ROUTES_DIR.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "x-forwarded-for" in source.lower() or "AuditRequestContext(" in source:
            offenders.append(path.name)
    assert sorted(offenders) == ["audit_context.py"]
