"""Tests de rutas OAuth Google Calendar (callback público)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def test_oauth_callback_invalid_state_redirects(client: TestClient) -> None:
    with patch(
        "app.routes.web.integrations.consume_state",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = client.get(
            "/auth/google/callback",
            params={"code": "fake-code", "state": "a" * 64},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == "/settings/integrations?error=oauth_state"
