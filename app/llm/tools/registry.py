"""Registro de tools LLM por familia (document | knowledge | calendar)."""

from __future__ import annotations

import enum
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.db import set_tenant_context
from app.core.errors import ValidationError


class ToolFamily(enum.StrEnum):
    document = "document"
    knowledge = "knowledge"
    calendar = "calendar"


@dataclass(frozen=True, slots=True)
class ToolCitation:
    source: Literal["document", "knowledge"]
    id: str
    label: str
    snippet: str | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    data: dict[str, object]
    citations: list[ToolCitation]
    error: str | None = None

    def to_tool_message_content(self) -> str:
        payload: dict[str, object] = {
            "ok": self.ok,
            "data": self.data,
            "citations": [
                {
                    "source": c.source,
                    "id": c.id,
                    "label": c.label,
                    "snippet": c.snippet,
                }
                for c in self.citations
            ],
        }
        if self.error:
            payload["error"] = self.error
        return json.dumps(payload, ensure_ascii=False, default=str)


@dataclass(frozen=True, slots=True)
class ToolContext:
    db: AsyncSession
    tenant_id: UUID
    user_id: UUID | None = None
    thread_id: UUID | None = None
    langfuse_trace_id: str | None = None


ToolExecutor = Callable[..., Awaitable[ToolResult]]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    family: ToolFamily
    description: str
    parameters_model: type[BaseModel]
    executor: ToolExecutor
    enabled: bool = True


@dataclass
class ToolRegistry:
    """Registro mutable de tools; una instancia por modo de chat.

    ``allowed_families``: si se especifica, ``execute()`` rechaza cualquier tool
    cuya familia no esté en el conjunto, incluso si está registrada. Úsalo en
    registries de canal externo para garantizar que nunca se ejecuten tools
    documentales aunque el LLM intente inyectarlas.
    """

    _tools: dict[str, ToolDefinition] = field(default_factory=dict)
    allowed_families: frozenset[ToolFamily] | None = None

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            msg = f"Tool already registered: {tool.name!r}"
            raise ValueError(msg)
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_definitions(
        self,
        *,
        families: set[ToolFamily] | None = None,
    ) -> list[ToolDefinition]:
        tools = list(self._tools.values())
        if families is not None:
            tools = [t for t in tools if t.family in families]
        return [t for t in tools if t.enabled]

    def list_for_llm(self, *, families: set[ToolFamily] | None = None) -> list[ToolDefinition]:
        settings = get_settings()
        selected = self.list_definitions(families=families)
        if settings.knowledge_tools_enabled:
            return selected
        return [t for t in selected if t.family != ToolFamily.knowledge]

    def to_anthropic_tools(
        self, *, families: set[ToolFamily] | None = None
    ) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": _parameters_schema(tool.parameters_model),
            }
            for tool in self.list_for_llm(families=families)
        ]

    def to_gemini_tools(self, *, families: set[ToolFamily] | None = None) -> list[Any]:
        from google.genai import types

        declarations = [
            types.FunctionDeclaration(
                name=tool.name,
                description=tool.description,
                parameters=cast(Any, _gemini_parameters_schema(tool.parameters_model)),
            )
            for tool in self.list_for_llm(families=families)
        ]
        if not declarations:
            return []
        return [types.Tool(function_declarations=declarations)]

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        ctx: ToolContext,
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                ok=False,
                data={},
                citations=[],
                error=f"Unknown tool: {name!r}",
            )

        # Capa 2 de guardrail: rechaza tools fuera de la familia permitida aunque
        # estuvieran registradas (defensa en profundidad contra bugs futuros o
        # inyecciones que intenten escalar a tools documentales en contexto de canal).
        if self.allowed_families is not None and tool.family not in self.allowed_families:
            return ToolResult(
                ok=False,
                data={"error": "tool_not_allowed_in_context", "tool": name},
                citations=[],
                error=f"Tool {name!r} is not available in this context",
            )

        settings = get_settings()
        if tool.family == ToolFamily.knowledge and not settings.knowledge_tools_enabled:
            return ToolResult(
                ok=False,
                data={
                    "error": "knowledge_not_available",
                    "hint": (
                        "El módulo de base de conocimiento aún no está activo en este entorno."
                    ),
                },
                citations=[],
                error="knowledge_not_available",
            )

        if not tool.enabled:
            return ToolResult(
                ok=False,
                data={"error": "tool_disabled", "tool": name},
                citations=[],
                error=f"Tool {name!r} is disabled",
            )

        try:
            args_model = tool.parameters_model.model_validate(arguments)
        except PydanticValidationError as exc:
            return ToolResult(
                ok=False,
                data={"validation_errors": exc.errors()},
                citations=[],
                error="invalid_tool_arguments",
            )

        await set_tenant_context(ctx.db, str(ctx.tenant_id))
        try:
            return await tool.executor(ctx, args_model)
        except ValidationError as exc:
            return ToolResult(
                ok=False,
                data={"details": exc.details, "message": exc.message},
                citations=[],
                error=exc.message,
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                data={},
                citations=[],
                error=str(exc)[:500],
            )


def _parameters_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    defs = schema.pop("$defs", None)
    if defs:
        schema = _inline_json_schema(schema, defs)
    schema.pop("title", None)
    return schema


def _gemini_parameters_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema compatible con Gemini function calling (sin claves rechazadas por la API)."""
    return cast(dict[str, Any], _strip_gemini_unsupported_schema_keys(_parameters_schema(model)))


def _strip_gemini_unsupported_schema_keys(node: Any) -> Any:
    """Elimina campos que el endpoint de tools de Gemini no acepta (p. ej. ``additionalProperties``)."""
    if isinstance(node, dict):
        cleaned = {
            k: _strip_gemini_unsupported_schema_keys(v)
            for k, v in node.items()
            if k not in {"additionalProperties", "title", "$schema"}
        }
        return cleaned
    if isinstance(node, list):
        return [_strip_gemini_unsupported_schema_keys(item) for item in node]
    return node


def get_tools_for_chat(
    *,
    settings: Settings | None = None,
    registry: ToolRegistry | None = None,
    tenant_id: UUID | None = None,
    db: AsyncSession | None = None,
) -> list[ToolDefinition]:
    """Tools expuestas al LLM en ``/chat`` según ``knowledge_tools_enabled`` (Paso 20).

    Siempre incluye tools documentales; añade familia ``knowledge`` si el flag está activo.
    ``tenant_id`` y ``db`` se reservan para overrides por tenant en ``tenants.settings``.
    """
    _ = tenant_id, db
    from app.llm.tools.document_chat import build_document_chat_registry

    reg = registry or build_document_chat_registry()
    s = settings or get_settings()
    tools = reg.list_definitions()
    if not s.knowledge_tools_enabled:
        tools = [t for t in tools if t.family != ToolFamily.knowledge]
    return [t for t in tools if t.enabled]


def _inline_json_schema(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            key = ref.removeprefix("#/$defs/")
            return _inline_json_schema(defs[key], defs)
        return {k: _inline_json_schema(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_json_schema(item, defs) for item in node]
    return node
