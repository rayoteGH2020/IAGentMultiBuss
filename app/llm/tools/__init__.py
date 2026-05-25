"""Tools tipadas para el chat documental y futuro RAG."""

from app.llm.tools.document_chat import build_document_chat_registry
from app.llm.tools.registry import (
    ToolCitation,
    ToolContext,
    ToolDefinition,
    ToolFamily,
    ToolRegistry,
    ToolResult,
)

__all__ = [
    "ToolCitation",
    "ToolContext",
    "ToolDefinition",
    "ToolFamily",
    "ToolRegistry",
    "ToolResult",
    "build_document_chat_registry",
]
