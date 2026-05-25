"""Tests del despachador de tools del chat."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.llm.tools.registry import ToolContext, ToolRegistry, ToolResult
from app.services import chat_tool_runner


@pytest.mark.asyncio
async def test_execute_tool_delegates_to_registry() -> None:
    registry = ToolRegistry()
    expected = ToolResult(ok=True, data={"n": 1}, citations=[])

    async def fake_execute(
        name: str,
        args: object,
        ctx: ToolContext,
    ) -> ToolResult:
        assert name == "ping"
        assert ctx.tenant_id == tenant_id
        return expected

    registry.execute = AsyncMock(side_effect=fake_execute)  # type: ignore[method-assign]

    tenant_id = uuid4()
    result = await chat_tool_runner.execute_tool(
        "ping",
        {},
        db=AsyncMock(),
        tenant_id=tenant_id,
        user_id=uuid4(),
        registry=registry,
    )
    assert result is expected
    registry.execute.assert_awaited_once()


def test_get_chat_registry_has_document_tools() -> None:
    registry = chat_tool_runner.get_chat_registry()
    names = {t.name for t in registry.list_for_llm()}
    assert "search_documents" in names
