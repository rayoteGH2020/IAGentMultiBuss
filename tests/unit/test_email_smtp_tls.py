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


@pytest.mark.asyncio
async def test_send_email_with_html_alternative_and_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.email import EmailAttachment

    settings = MagicMock(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="",
        smtp_password=MagicMock(get_secret_value=lambda: ""),
        smtp_from="noreply@example.com",
        smtp_ssl=False,
        smtp_starttls=True,
    )
    monkeypatch.setattr("app.core.email.get_settings", lambda: settings)
    send = AsyncMock(return_value=({}, "OK"))
    monkeypatch.setattr("app.core.email.aiosmtplib.send", send)

    await send_email(
        to="dest@example.com",
        subject="Hi",
        body="Plain",
        html="<p>Rich</p>",
        attachments=[EmailAttachment(filename="año.txt", content_type="text/plain", data=b"x")],
    )

    msg = send.await_args.args[0]
    assert msg.get_content_type() == "multipart/mixed"
    body = msg.get_body(preferencelist=("html",))
    assert body is not None and "<p>Rich</p>" in body.get_content()
    assert msg.get_body(preferencelist=("plain",)).get_content().strip() == "Plain"
    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "año.txt"
    assert attachments[0].get_payload(decode=True) == b"x"
