from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _set_required_env(monkeypatch: pytest.MonkeyPatch, *, app_env: str) -> None:
    from app.config import get_settings

    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("APP_BASE_URL", "https://testserver")
    get_settings.cache_clear()


def test_security_headers_are_added_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch, app_env="development")

    from app.config import get_settings
    from app.main import create_app

    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "https://*.clerk.accounts.dev" in csp
    assert "connect-src" in csp
    # Alpine build estándar (new Function) — ver security_headers.py.
    assert "'unsafe-eval'" in csp
    assert "Strict-Transport-Security" not in response.headers
    get_settings.cache_clear()


def test_build_csp_includes_clerk_frontend_host() -> None:
    from app.core.security_headers import build_content_security_policy

    csp = build_content_security_policy(
        clerk_frontend_host="https://ample-mink-12.clerk.accounts.dev/.well-known/jwks.json",
    )
    assert "https://ample-mink-12.clerk.accounts.dev" in csp
    assert "script-src" in csp
    assert "'unsafe-eval'" in csp
    assert "frame-src" in csp
    assert "worker-src 'self' blob:" in csp
    assert "upgrade-insecure-requests" not in csp


def test_build_csp_upgrade_insecure_only_when_requested() -> None:
    from app.core.security_headers import build_content_security_policy

    assert "upgrade-insecure-requests" not in build_content_security_policy()
    assert "upgrade-insecure-requests" in build_content_security_policy(
        upgrade_insecure_requests=True,
    )


def test_docs_are_disabled_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch, app_env="production")

    from app.config import get_settings
    from app.main import create_app

    with TestClient(create_app(), base_url="https://testserver") as client:
        docs = client.get("/docs")
        redoc = client.get("/redoc")
        openapi_alias = client.get("/openapi")
        openapi = client.get("/openapi.json")

    assert docs.status_code == 404
    assert redoc.status_code == 404
    assert openapi_alias.status_code == 404
    assert openapi.status_code == 404
    get_settings.cache_clear()


def test_production_redirects_http_to_https(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch, app_env="production")

    from app.config import get_settings
    from app.main import create_app

    with TestClient(create_app(), base_url="http://testserver") as client:
        response = client.get("/health", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "https://testserver/health"
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
    get_settings.cache_clear()


def test_untrusted_host_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch, app_env="production")

    from app.config import get_settings
    from app.main import create_app

    with TestClient(create_app(), base_url="https://evil.test") as client:
        response = client.get("/health")

    assert response.status_code == 400
    get_settings.cache_clear()
