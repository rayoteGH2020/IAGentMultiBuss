"""Tests unitarios del job ARQ process_channel_message (Paso 21 / Fase D)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.jobs import channel_jobs
from app.models.channel_integration import ChannelIntegrationStatus
from app.schemas.channel import ChannelResponse
from app.services.audit_service import (
    ACTION_CHANNEL_ESCALATED,
    ACTION_CHANNEL_MESSAGE_RECEIVED,
    ACTION_CHANNEL_MESSAGE_SENT,
)

pytestmark = pytest.mark.asyncio

TENANT_ID = uuid4()
INTEGRATION_ID = uuid4()


def _fake_tenant() -> MagicMock:
    tenant = MagicMock()
    tenant.id = TENANT_ID
    tenant.name = "Clínica Demo"
    return tenant


def _fake_integration(*, threshold: float = 0.6) -> MagicMock:
    integ = MagicMock()
    integ.status = ChannelIntegrationStatus.active.value
    integ.confidence_threshold = threshold
    integ.phone_number_id = "123456789"
    return integ


def _session_factory(db: AsyncMock) -> Any:
    @asynccontextmanager
    async def _factory(_tenant_id: object) -> Any:
        yield db

    return _factory


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


async def test_process_channel_message_sends_rag_on_high_confidence(mock_db: AsyncMock) -> None:
    tenant = _fake_tenant()
    tenant_result = AsyncMock()
    tenant_result.scalar_one_or_none = MagicMock(return_value=tenant)
    mock_db.execute = AsyncMock(return_value=tenant_result)

    response = ChannelResponse(text="Abrimos de 9 a 18h.", confidence=0.85, citations_count=1)
    mock_send = AsyncMock()

    with (
        patch(
            "app.jobs.channel_jobs.session_factory_for_worker",
            _session_factory(mock_db),
        ),
        patch(
            "app.jobs.channel_jobs.channel_integration_service.get_integration",
            AsyncMock(return_value=_fake_integration(threshold=0.6)),
        ),
        patch(
            "app.jobs.channel_jobs.channel_integration_service.decrypt_api_token",
            return_value="token-wa",
        ),
        patch("app.jobs.channel_jobs._get_admin_email", AsyncMock(return_value=None)),
        patch("app.jobs.channel_jobs._check_rate_limit", AsyncMock(return_value=True)),
        patch(
            "app.jobs.channel_jobs.channel_chat_service.answer_for_channel",
            AsyncMock(return_value=response),
        ),
        patch("app.jobs.channel_jobs.audit_service.log_action", AsyncMock()) as mock_audit,
        patch("app.jobs.channel_jobs._safe_send", mock_send),
    ):
        await channel_jobs.process_channel_message(
            {"redis": AsyncMock(), "job_try": 1},
            str(TENANT_ID),
            "whatsapp",
            "34600000001",
            "¿Cuál es vuestro horario?",
            str(INTEGRATION_ID),
        )

    mock_send.assert_awaited_once()
    assert mock_send.await_args.args[2] == "Abrimos de 9 a 18h."
    audit_actions = [call.kwargs["action"] for call in mock_audit.await_args_list]
    assert ACTION_CHANNEL_MESSAGE_RECEIVED in audit_actions
    assert ACTION_CHANNEL_MESSAGE_SENT in audit_actions
    assert ACTION_CHANNEL_ESCALATED not in audit_actions


async def test_process_channel_message_escalates_on_low_confidence(mock_db: AsyncMock) -> None:
    tenant = _fake_tenant()
    tenant_result = AsyncMock()
    tenant_result.scalar_one_or_none = MagicMock(return_value=tenant)
    mock_db.execute = AsyncMock(return_value=tenant_result)

    response = ChannelResponse(text="No sé.", confidence=0.0, citations_count=0)
    mock_send = AsyncMock()

    with (
        patch(
            "app.jobs.channel_jobs.session_factory_for_worker",
            _session_factory(mock_db),
        ),
        patch(
            "app.jobs.channel_jobs.channel_integration_service.get_integration",
            AsyncMock(return_value=_fake_integration(threshold=0.6)),
        ),
        patch(
            "app.jobs.channel_jobs.channel_integration_service.decrypt_api_token",
            return_value="token-tg",
        ),
        patch("app.jobs.channel_jobs._get_admin_email", AsyncMock(return_value=None)),
        patch("app.jobs.channel_jobs._check_rate_limit", AsyncMock(return_value=True)),
        patch(
            "app.jobs.channel_jobs.channel_chat_service.answer_for_channel",
            AsyncMock(return_value=response),
        ),
        patch("app.jobs.channel_jobs.audit_service.log_action", AsyncMock()) as mock_audit,
        patch("app.jobs.channel_jobs._safe_send", mock_send),
    ):
        await channel_jobs.process_channel_message(
            {"redis": AsyncMock(), "job_try": 1},
            str(TENANT_ID),
            "telegram",
            "123456789",
            "¿Tenéis parking?",
            str(INTEGRATION_ID),
        )

    mock_send.assert_awaited_once()
    sent_text = mock_send.await_args.args[2]
    assert "no tengo información" in sent_text.lower()
    assert "Clínica Demo" in sent_text
    audit_actions = [call.kwargs["action"] for call in mock_audit.await_args_list]
    assert ACTION_CHANNEL_ESCALATED in audit_actions
    assert ACTION_CHANNEL_MESSAGE_SENT not in audit_actions


async def test_process_channel_message_rate_limit_sends_limit_message(mock_db: AsyncMock) -> None:
    tenant = _fake_tenant()
    tenant_result = AsyncMock()
    tenant_result.scalar_one_or_none = MagicMock(return_value=tenant)
    mock_db.execute = AsyncMock(return_value=tenant_result)

    mock_send = AsyncMock()
    mock_answer = AsyncMock()

    with (
        patch(
            "app.jobs.channel_jobs.session_factory_for_worker",
            _session_factory(mock_db),
        ),
        patch(
            "app.jobs.channel_jobs.channel_integration_service.get_integration",
            AsyncMock(return_value=_fake_integration()),
        ),
        patch(
            "app.jobs.channel_jobs.channel_integration_service.decrypt_api_token",
            return_value="token-wa",
        ),
        patch("app.jobs.channel_jobs._get_admin_email", AsyncMock(return_value=None)),
        patch("app.jobs.channel_jobs._check_rate_limit", AsyncMock(return_value=False)),
        patch("app.jobs.channel_jobs.channel_chat_service.answer_for_channel", mock_answer),
        patch("app.jobs.channel_jobs._safe_send", mock_send),
    ):
        await channel_jobs.process_channel_message(
            {"redis": AsyncMock(), "job_try": 1},
            str(TENANT_ID),
            "whatsapp",
            "34600000099",
            "Spam",
            str(INTEGRATION_ID),
        )

    mock_answer.assert_not_awaited()
    mock_send.assert_awaited_once()
    assert "demasiados mensajes" in mock_send.await_args.args[2].lower()
