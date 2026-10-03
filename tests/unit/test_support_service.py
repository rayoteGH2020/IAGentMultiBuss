"""Soporte técnico: validación del formulario, email al SADM, cupo diario y auditoría."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.core.email import EmailAttachment
from app.core.errors import ExternalServiceError, RateLimitError, ValidationError
from app.schemas.support import SupportRequestCreate, SupportRequestKind, SupportSeverity
from app.services import support_service
from pydantic import ValidationError as PydanticValidationError


def _payload(**overrides: Any) -> SupportRequestCreate:
    data: dict[str, Any] = {
        "title": "No carga el calendario",
        "message": "Desde ayer <b>falla</b> al abrir.",
        "kind": "error",
        "severity": "critical",
    }
    data.update(overrides)
    return SupportRequestCreate.model_validate(data)


def _tenant() -> Any:
    return SimpleNamespace(
        id=uuid4(), name="Clínica  Sol", clerk_org_id="org_1", plan_code="pro", plan="pro"
    )


def _user() -> Any:
    return SimpleNamespace(id=uuid4(), name="Ana Admin", email="ana@example.com")


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    async def incrby(self, key: str, delta: int) -> int:
        self.store[key] = self.store.get(key, 0) + delta
        return self.store[key]

    async def decrby(self, key: str, delta: int) -> int:
        return await self.incrby(key, -delta)

    async def expire(self, key: str, ttl: int) -> bool:
        return True


# ── Schema ──────────────────────────────────────────────────────────────


def test_payload_title_single_line_and_message_newlines_normalized() -> None:
    payload = _payload(title="  Hola\r\nBcc: x@y.z ", message="uno\r\ndos\r\n")
    assert payload.title == "Hola Bcc: x@y.z"
    assert payload.message == "uno\ndos"
    assert payload.kind is SupportRequestKind.ERROR
    assert payload.severity is SupportSeverity.CRITICAL


def test_payload_message_limit_counts_crlf_as_one_char() -> None:
    # 2500 caracteres en crudo; 2000 tras normalizar \r\n (lo que cuenta el navegador).
    payload = _payload(message="a\r\n" * 500 + "a" * 1000)
    assert len(payload.message) == 2000


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", ""),
        ("title", "x" * 101),
        ("message", "   "),
        ("message", "x" * 2001),
        ("kind", "queja"),
        ("severity", "urgente"),
        ("kind", ""),
    ],
)
def test_payload_rejects_invalid_fields(field: str, value: str) -> None:
    with pytest.raises(PydanticValidationError) as exc:
        _payload(**{field: value})
    assert exc.value.errors()[0]["loc"][0] == field


# ── Email ───────────────────────────────────────────────────────────────


def test_email_shows_severity_org_member_and_escapes_content() -> None:
    tenant, user = _tenant(), _user()
    attachment = EmailAttachment(filename="log.txt", content_type="text/plain", data=b"x")
    email = support_service.build_support_email(
        tenant=tenant,
        user=user,
        actor_role="admin",
        payload=_payload(),
        attachment=attachment,
        sent_at=datetime(2026, 9, 28, 10, 0, tzinfo=UTC),
    )

    assert email.subject == "[Soporte técnico][CRÍTICA] No carga el calendario · Clínica Sol"
    assert email.body.startswith("GRAVEDAD: CRÍTICA\nTipo: Error")
    for text in ("Clínica Sol", str(tenant.id), "Ana Admin", "ana@example.com", "log.txt"):
        assert text in email.body
        assert text in email.html
    assert "Administrador" in email.body
    assert "GRAVEDAD: CRÍTICA" in email.html
    assert "#dc2626" in email.html  # rojo de crítica
    assert "&lt;b&gt;falla&lt;/b&gt;" in email.html
    assert "<b>falla</b>" not in email.html


@pytest.mark.parametrize(
    ("severity", "label", "color"),
    [("low", "BAJA", "#16a34a"), ("medium", "MEDIA", "#ca8a04"), ("high", "ALTA", "#ea580c")],
)
def test_email_severity_colors(severity: str, label: str, color: str) -> None:
    email = support_service.build_support_email(
        tenant=_tenant(),
        user=_user(),
        actor_role="admin",
        payload=_payload(severity=severity),
        attachment=None,
        sent_at=datetime(2026, 9, 28, tzinfo=UTC),
    )
    assert f"[{label}]" in email.subject
    assert color in email.html
    assert "Adjunto: ninguno" in email.body


# ── Envío ───────────────────────────────────────────────────────────────


@pytest.fixture
def smtp_ok(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    settings = MagicMock(email_sadm="sadm@example.com", smtp_host="smtp.example.com")
    monkeypatch.setattr(support_service, "get_settings", lambda: settings)
    send = AsyncMock()
    monkeypatch.setattr(support_service, "send_email", send)
    return send


@pytest.fixture
def audit(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    log_action = AsyncMock()
    monkeypatch.setattr(support_service.audit_service, "log_action", log_action)
    return log_action


async def _send(redis: _FakeRedis, attachment: EmailAttachment | None = None) -> None:
    await support_service.send_support_request(
        MagicMock(),
        tenant=_tenant(),
        user=_user(),
        actor_role="admin",
        payload=_payload(),
        attachment=attachment,
        redis=redis,
    )


@pytest.mark.asyncio
async def test_send_emails_sadm_with_attachment_and_audits_metadata_only(
    smtp_ok: AsyncMock, audit: AsyncMock
) -> None:
    attachment = EmailAttachment(filename="a.jpg", content_type="image/jpeg", data=b"\xff\xd8\xff")
    await _send(_FakeRedis(), attachment)

    kwargs = smtp_ok.await_args.kwargs
    assert kwargs["to"] == "sadm@example.com"
    assert kwargs["attachments"] == [attachment]
    assert "GRAVEDAD" in kwargs["html"]
    meta = audit.await_args.kwargs["metadata"]
    assert meta == {
        "kind": "error",
        "severity": "critical",
        "has_attachment": True,
        "attachment_bytes": 3,
    }
    assert audit.await_args.kwargs["action"] == support_service.ACTION_SUPPORT_REQUEST_SENT


@pytest.mark.asyncio
async def test_send_rate_limited_after_daily_max(smtp_ok: AsyncMock, audit: AsyncMock) -> None:
    redis = _FakeRedis()
    tenant = _tenant()
    for _ in range(support_service.SUPPORT_REQUESTS_MAX_PER_DAY):
        await support_service.send_support_request(
            MagicMock(), tenant=tenant, user=_user(), actor_role="admin",
            payload=_payload(), attachment=None, redis=redis,
        )  # fmt: skip
    with pytest.raises(RateLimitError):
        await support_service.send_support_request(
            MagicMock(), tenant=tenant, user=_user(), actor_role="admin",
            payload=_payload(), attachment=None, redis=redis,
        )  # fmt: skip
    assert smtp_ok.await_count == support_service.SUPPORT_REQUESTS_MAX_PER_DAY


@pytest.mark.asyncio
async def test_send_failure_releases_quota_and_skips_audit(
    smtp_ok: AsyncMock, audit: AsyncMock
) -> None:
    smtp_ok.side_effect = OSError("smtp down")
    redis = _FakeRedis()
    with pytest.raises(ExternalServiceError) as exc:
        await _send(redis)
    assert exc.value.details == {"code": "support_send_failed"}
    assert set(redis.store.values()) == {0}
    audit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("email_sadm", "smtp_host", "error", "code"),
    [
        ("", "smtp.example.com", ValidationError, "email_sadm_missing"),
        ("sadm@example.com", " ", ExternalServiceError, "smtp_not_configured"),
    ],
)
async def test_send_missing_config_fails_before_consuming_quota(
    monkeypatch: pytest.MonkeyPatch,
    email_sadm: str,
    smtp_host: str,
    error: type[Exception],
    code: str,
) -> None:
    settings = MagicMock(email_sadm=email_sadm, smtp_host=smtp_host)
    monkeypatch.setattr(support_service, "get_settings", lambda: settings)
    redis = _FakeRedis()
    with pytest.raises(error) as exc:
        await _send(redis)
    assert exc.value.details == {"code": code}  # type: ignore[attr-defined]
    assert redis.store == {}
