"""Tools tipadas para el chat documental y RAG."""

from app.llm.tools.document_chat import build_document_chat_registry
from app.llm.tools.registry import (
    ToolCitation,
    ToolContext,
    ToolDefinition,
    ToolFamily,
    ToolRegistry,
    ToolResult,
    get_tools_for_chat,
)

__all__ = [
    "ToolCitation",
    "ToolContext",
    "ToolDefinition",
    "ToolFamily",
    "ToolRegistry",
    "ToolResult",
    "build_document_chat_registry",
    "get_tools_for_chat",
]
