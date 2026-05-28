"""Versión de prompt y construcción del system prompt del chat."""

from __future__ import annotations

from app.config import Settings, get_settings
from app.llm.prompts_loader import load_prompt, render_prompt

PROMPT_DOCUMENTS = "chat_documents_v1"
PROMPT_UNIFIED = "chat_unified_v1"


def resolve_chat_prompt_version(settings: Settings | None = None) -> str:
    """Devuelve la versión de prompt según el flag de tools de conocimiento."""
    s = settings or get_settings()
    return PROMPT_UNIFIED if s.knowledge_tools_enabled else PROMPT_DOCUMENTS


def build_chat_system_prompt(*, company_name: str, settings: Settings | None = None) -> str:
    """Carga y renderiza el system prompt del chat para el tenant."""
    version = resolve_chat_prompt_version(settings)
    if version == PROMPT_UNIFIED:
        return render_prompt(version, company_name=company_name)
    return load_prompt(version)
