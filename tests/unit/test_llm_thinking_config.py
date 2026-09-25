"""Thinking de Gemini por tarea y contabilidad de sus tokens."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.llm.client import LLMClient, TaskType, _extract_token_usage, _google_thinking_config


@pytest.mark.parametrize(
    ("task", "model", "expected"),
    [
        ("extraction", "gemini-3.8-flash", {"thinking_level": "low"}),
        ("extraction", "gemini-3.1-flash-lite", {"thinking_level": "low"}),
        ("extraction", "gemini-2.5-flash", {"thinking_budget": 0}),
        ("extraction", "gemini-2.5-flash-lite", {"thinking_budget": 0}),
        # Pro no admite desactivar el razonamiento.
        ("extraction", "gemini-2.5-pro", None),
        ("extraction", "gemini-3-pro-preview", None),
        # Solo las tareas marcadas bajan el thinking.
        ("chat", "gemini-3.8-flash", None),
        ("transcription", "gemini-2.5-flash", None),
    ],
)
def test_google_thinking_config(
    task: TaskType, model: str, expected: dict[str, Any] | None
) -> None:
    assert _google_thinking_config(task, model) == expected


def test_gemini_usage_counts_thinking_as_output() -> None:
    raw = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=3000,
            candidates_token_count=400,
            thoughts_token_count=2600,
        )
    )
    assert _extract_token_usage(raw) == (3000, 3000)


def test_gemini_usage_without_thinking_field() -> None:
    raw = SimpleNamespace(
        usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=50)
    )
    assert _extract_token_usage(raw) == (100, 50)


def _client_with_fake_google() -> tuple[LLMClient, AsyncMock]:
    client = LLMClient.__new__(LLMClient)
    create = AsyncMock(return_value=(MagicMock(), MagicMock()))
    client._google = MagicMock()
    client._google.chat.completions.create_with_completion = create
    return client, create


@pytest.mark.asyncio
async def test_extraction_call_sends_thinking_config() -> None:
    client, create = _client_with_fake_google()

    await client._call_sdk_once(
        task="extraction",
        provider="google",
        model="gemini-3.8-flash",
        typed_messages=[],
        response_model=MagicMock(),
        max_retries=2,
    )

    assert create.await_args is not None
    assert create.await_args.kwargs["thinking_config"] == {"thinking_level": "low"}


@pytest.mark.asyncio
async def test_chat_call_keeps_default_thinking() -> None:
    client, create = _client_with_fake_google()

    await client._call_sdk_once(
        task="chat",
        provider="google",
        model="gemini-3.8-flash",
        typed_messages=[],
        response_model=MagicMock(),
        max_retries=2,
    )

    assert create.await_args is not None
    assert "thinking_config" not in create.await_args.kwargs
