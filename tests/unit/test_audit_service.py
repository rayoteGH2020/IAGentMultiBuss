"""Tests unitarios del servicio de auditoría."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.services import audit_service
from app.services.audit_service import (
    ACTION_CHAT_MESSAGE_SENT,
    ACTION_CHAT_TOOL_EXECUTED,
    AuditRequestContext,
)

pytestmark = pytest.mark.asyncio


async def test_log_chat_user_message_adds_row() -> None:
    db = AsyncMock()
    db.add = lambda _row: None
    db.flush = AsyncMock()

    tenant_id = uuid4()
    user_id = uuid4()
    thread_id = uuid4()
    message_id = uuid4()

    row = await audit_service.log_chat_user_message(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        thread_id=thread_id,
        message_id=message_id,
        content_length=42,
        request_ctx=AuditRequestContext(ip="127.0.0.1", user_agent="pytest"),
    )

    assert row.action == ACTION_CHAT_MESSAGE_SENT
    assert row.tenant_id == tenant_id
    assert row.user_id == user_id
    assert row.resource_id == message_id
    assert row.metadata_ is not None
    assert row.metadata_["thread_id"] == str(thread_id)
    assert row.metadata_["content_length"] == 42
    assert row.ip == "127.0.0.1"
    db.flush.assert_awaited_once()


async def test_log_chat_tool_executed_adds_row() -> None:
    db = AsyncMock()
    db.add = lambda _row: None
    db.flush = AsyncMock()

    tenant_id = uuid4()
    thread_id = uuid4()
    llm_call_id = uuid4()

    row = await audit_service.log_chat_tool_executed(
        db,
        tenant_id=tenant_id,
        user_id=uuid4(),
        thread_id=thread_id,
        tool_name="search_documents",
        ok=True,
        cost_eur=0.0025,
        llm_call_id=llm_call_id,
    )

    assert row.action == ACTION_CHAT_TOOL_EXECUTED
    assert row.resource_type == "chat_thread"
    assert row.resource_id == thread_id
    assert row.metadata_ is not None
    assert row.metadata_["tool_name"] == "search_documents"
    assert row.metadata_["ok"] is True
    assert row.metadata_["cost_eur"] == 0.0025
    assert row.metadata_["llm_call_id"] == str(llm_call_id)
