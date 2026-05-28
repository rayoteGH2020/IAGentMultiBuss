"""Prompt unificado del chat (Paso 20 Fase B)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from app.llm.chat_prompts import (
    PROMPT_DOCUMENTS,
    PROMPT_UNIFIED,
    build_chat_system_prompt,
    resolve_chat_prompt_version,
)
from app.llm.prompts_loader import load_prompt


def test_resolve_chat_prompt_version_unified_when_knowledge_enabled() -> None:
    assert resolve_chat_prompt_version() == PROMPT_UNIFIED


def test_resolve_chat_prompt_version_documents_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.llm.chat_prompts.get_settings",
        lambda: MagicMock(knowledge_tools_enabled=False),
    )
    assert resolve_chat_prompt_version() == PROMPT_DOCUMENTS


def test_chat_unified_prompt_file_exists() -> None:
    text = load_prompt(PROMPT_UNIFIED)
    assert "list_knowledge_sources" in text
    assert "search_knowledge" in text
    assert "search_documents" in text
    assert "{company_name}" in text
    assert "Fuentes:" in text


def test_build_chat_system_prompt_injects_company_name() -> None:
    prompt = build_chat_system_prompt(company_name="Panadería López")
    assert "Panadería López" in prompt
    assert "{company_name}" not in prompt
    assert "search_knowledge" in prompt


def test_build_chat_system_prompt_documents_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.llm.chat_prompts.get_settings",
        lambda: MagicMock(knowledge_tools_enabled=False),
    )
    prompt = build_chat_system_prompt(company_name="Acme SL")
    assert "Acme SL" not in prompt  # documents prompt has no placeholder
    assert "search_knowledge" not in prompt
    assert "list_doc_types" in prompt
