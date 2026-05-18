"""Unit-ish: el endpoint /metrics/module1 rechaza sin token válido (no tocar BD)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.errors import register_error_handlers
from app.routes.api import metrics as metrics_router
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    import pytest


def _make_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(metrics_router.router)
    return app


def test_metrics_module1_missing_token_is_422() -> None:
    with TestClient(_make_app(), raise_server_exceptions=True) as client:
        response = client.get("/metrics/module1")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][1].lower() == "x-metrics-token"


def test_metrics_module1_bad_token_is_403(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings as _gs

    _gs.cache_clear()
    monkeypatch.setenv("APP_SECRET_KEY", "x")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("METRICS_TOKEN", "right-token")
    _gs.cache_clear()

    with TestClient(_make_app(), raise_server_exceptions=False) as client:
        response = client.get("/metrics/module1", headers={"X-Metrics-Token": "wrong"})
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"

    _gs.cache_clear()
