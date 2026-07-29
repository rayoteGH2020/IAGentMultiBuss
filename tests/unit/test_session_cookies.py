"""Tests de helpers de cookies de sesión Clerk."""

from app.config import Settings
from app.core.session_cookies import (
    CLERK_SESSION_COOKIE_NAME,
    clear_clerk_session_cookie,
)
from starlette.responses import Response


def _minimal_settings(*, app_env: str) -> Settings:
    return Settings(
        app_secret_key="test-secret-key-minimum-length",  # pragma: allowlist secret
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379/0",
        app_env=app_env,  # type: ignore[arg-type]
    )


def test_clear_clerk_session_cookie_prod_uses_secure_flags() -> None:
    response = Response()
    clear_clerk_session_cookie(response, _minimal_settings(app_env="production"))

    set_cookies = response.raw_headers
    cookie_headers = [v.decode() for k, v in set_cookies if k.decode().lower() == "set-cookie"]
    assert len(cookie_headers) == 1
    header = cookie_headers[0]
    assert f"{CLERK_SESSION_COOKIE_NAME}=" in header
    assert "Secure" in header
    assert "HttpOnly" in header
    assert "SameSite=lax" in header


def test_clear_clerk_session_cookie_dev_tries_secure_and_insecure() -> None:
    response = Response()
    clear_clerk_session_cookie(response, _minimal_settings(app_env="development"))

    cookie_headers = [
        v.decode() for k, v in response.raw_headers if k.decode().lower() == "set-cookie"
    ]
    assert len(cookie_headers) == 2
    assert any("Secure" in h for h in cookie_headers)
    assert any("Secure" not in h for h in cookie_headers)
