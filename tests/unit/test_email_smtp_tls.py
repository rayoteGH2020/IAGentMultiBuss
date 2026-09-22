"""Unit tests for SMTP TLS/SSL flag resolution."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from app.core.email import send_email, smtp_tls_flags


def test_smtp_tls_flags_ssl_on_465() -> None:
    settings = MagicMock(smtp_ssl=True, smtp_starttls=False)
    assert smtp_tls_flags(settings) == (True, False)


def test_smtp_tls_flags_starttls_on_587() -> None:
    settings = MagicMock(smtp_ssl=False, smtp_starttls=True)
    assert smtp_tls_flags(settings) == (False, True)


def test_smtp_tls_flags_ssl_wins_if_both_true() -> None:
    settings = MagicMock(smtp_ssl=True, smtp_starttls=True)
    assert smtp_tls_flags(settings) == (True, False)


@pytest.mark.asyncio
async def test_send_email_uses_ssl_without_starttls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = MagicMock(
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_user="user@example.com",
        smtp_password=MagicMock(get_secret_value=lambda: "secret"),
        smtp_from="noreply@example.com",
        smtp_ssl=True,
        smtp_starttls=False,
    )
    monkeypatch.setattr("app.core.email.get_settings", lambda: settings)
    send = AsyncMock(return_value=({}, "OK"))
    monkeypatch.setattr("app.core.email.aiosmtplib.send", send)

    await send_email(to="dest@example.com", subject="Hi", body="Body")

    assert send.await_count == 1
    kwargs = send.await_args.kwargs
    assert kwargs["hostname"] == "smtp.example.com"
    assert kwargs["port"] == 465
    assert kwargs["use_tls"] is True
    assert kwargs["start_tls"] is False
    assert kwargs["username"] == "user@example.com"
    assert kwargs["password"] == "secret"  # pragma: allowlist secret
