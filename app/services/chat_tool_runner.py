"""Despacho de tool calls del chat hacia el registry documental."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.llm.tools.document_chat import build_document_chat_registry
from app.llm.tools.registry import ToolContext, ToolRegistry, ToolResult, get_tools_for_chat

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


def get_chat_registry() -> ToolRegistry:
    """Registry completo del chat (documentales + knowledge registradas)."""
    return build_document_chat_registry()


def list_chat_tools_for_llm() -> list[str]:
    """Nombres de tools visibles para el LLM según configuración (Paso 20 Fase C)."""
    return [t.name for t in get_tools_for_chat()]


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    db: AsyncSession,
    tenant_id: UUID,
    user_id: UUID | None = None,
    registry: ToolRegistry | None = None,
) -> ToolResult:
    """Ejecuta una tool con contexto de tenant (RLS vía ``ToolContext``)."""
    reg = registry or get_chat_registry()
    ctx = ToolContext(db=db, tenant_id=tenant_id, user_id=user_id)
    return await reg.execute(name, arguments, ctx)
