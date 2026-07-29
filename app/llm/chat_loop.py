"""Loop de tool-calling para el task ``chat`` (Gemini / Anthropic)."""

from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import structlog
from anthropic.types import TextBlock, ToolUseBlock
from google import genai
from google.genai import types as genai_types
from langfuse.types import TraceContext

from app.config import Settings
from app.llm.observability import trace_messages, trace_status_message, trace_text
from app.llm.pricing import compute_cost_eur
from app.llm.tools.registry import ToolContext, ToolRegistry, ToolResult
from app.llm.tracing import get_langfuse
from app.models import LLMCall
from app.schemas.chat import ChatCitation
from app.services.chat_citations import (
    citations_to_json,
    extract_citations_from_tool_result,
    merge_citation_lists,
)

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

# Clave interna del historial in-memory: Content nativo de Gemini (con thought_signature).
_GEMINI_MODEL_CONTENT_KEY = "gemini_model_content"
# Campo serializable en tool_calls persistidos (base64) para rehidratar Parts.
_THOUGHT_SIGNATURE_KEY = "thought_signature"

_MAX_ITERATIONS_DEFAULT = 6
_EXHAUSTED_MESSAGE = (
    "No pude completar la consulta en el número máximo de pasos. "
    "Intenta reformular la pregunta o acotar el periodo."
)


@dataclass(frozen=True, slots=True)
class TurnMessageRecord:
    """Mensaje del turno a persistir en ``chat_messages`` (assistant o tool)."""

    role: str
    content: str | None = None
    tool_call: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None
    citations: list[dict[str, Any]] | None = None
    llm_call_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ToolLoopResult:
    final_text: str
    llm_call_ids: list[UUID]
    tool_calls_executed: list[str]
    turn_messages: tuple[TurnMessageRecord, ...] = ()
    citations: tuple[ChatCitation, ...] = ()
    knowledge_tools_used: bool = False


@dataclass(frozen=True, slots=True)
class _TurnOutcome:
    assistant_message: dict[str, Any]
    final_text: str | None
    tool_calls: list[dict[str, Any]]
    input_tokens: int
    output_tokens: int
    raw: Any


async def run_tool_loop(
    *,
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    registry: ToolRegistry,
    ctx: ToolContext,
    tenant_id: UUID,
    db: AsyncSession,
    prompt_version: str,
    max_iters: int = _MAX_ITERATIONS_DEFAULT,
    settings: Settings,
    anthropic_client: AsyncAnthropic,
    google_client: genai.Client,
) -> ToolLoopResult:
    """Ejecuta el loop LLM → tools hasta respuesta final o agotar iteraciones."""
    langfuse = get_langfuse()
    trace_uuid = langfuse.create_trace_id()
    trace_id_str = str(trace_uuid)
    trace_ctx = TraceContext(trace_id=trace_id_str)

    conversation = list(messages)
    llm_call_ids: list[UUID] = []
    tool_calls_executed: list[str] = []
    turn_messages: list[TurnMessageRecord] = []
    citation_batches: list[list[ChatCitation]] = []
    final_text: str | None = None
    knowledge_tools_used = False

    rag_obs = langfuse.start_observation(
        trace_context=trace_ctx,
        name="chat_rag_turn",
        as_type="span",
        metadata={
            "tenant_id": str(tenant_id),
            "thread_id": str(ctx.thread_id) if ctx.thread_id else None,
            "prompt_version": prompt_version,
            "knowledge_tools_used": False,
            "citations_count": 0,
        },
    )
    ctx = replace(ctx, langfuse_trace_id=trace_id_str)

    for iteration in range(max_iters):
        obs = langfuse.start_observation(
            trace_context=trace_ctx,
            name=f"llm.chat.iteration_{iteration + 1}",
            as_type="generation",
            metadata={
                "tenant_id": str(tenant_id),
                "prompt_version": prompt_version,
                "provider": provider,
                "iteration": iteration + 1,
            },
            model=model,
            input=trace_messages(conversation),
        )
        started = time.perf_counter()
        status = "ok"
        error: str | None = None
        error_type: str | None = None
        input_tokens = 0
        output_tokens = 0
        llm_call: LLMCall | None = None
        pending_records: list[TurnMessageRecord] = []
        tools_this_turn: list[tuple[str, ToolResult]] = []

        try:
            if provider == "anthropic":
                turn = await _anthropic_turn(
                    anthropic_client=anthropic_client,
                    model=model,
                    messages=conversation,
                    registry=registry,
                )
            else:
                turn = await _gemini_turn(
                    google_client=google_client,
                    model=model,
                    messages=conversation,
                    registry=registry,
                )
            input_tokens = turn.input_tokens
            output_tokens = turn.output_tokens
            conversation.append(turn.assistant_message)

            if turn.tool_calls:
                pending_records.append(
                    TurnMessageRecord(
                        role="assistant",
                        content=turn.assistant_message.get("content"),
                        tool_call={"calls": turn.tool_calls},
                    ),
                )
                for call in turn.tool_calls:
                    tool_calls_executed.append(call["name"])
                    result = await registry.execute(call["name"], call["arguments"], ctx)
                    tools_this_turn.append((call["name"], result))
                    if call["name"] in {
                        "search_knowledge",
                        "list_knowledge_sources",
                        "get_knowledge_chunk",
                    }:
                        knowledge_tools_used = True
                    if call["name"] == "search_knowledge" and result.ok:
                        citation_batches.append(
                            extract_citations_from_tool_result(
                                result.data,
                                settings=settings,
                            ),
                        )
                    tool_payload = result.to_tool_message_content()
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "name": call["name"],
                            "content": tool_payload,
                        },
                    )
                    pending_records.append(
                        TurnMessageRecord(
                            role="tool",
                            tool_call={
                                "id": call["id"],
                                "name": call["name"],
                                "arguments": call["arguments"],
                            },
                            tool_result=json.loads(tool_payload),
                        ),
                    )
            else:
                final_text = turn.final_text
                pending_records.append(
                    TurnMessageRecord(
                        role="assistant",
                        content=final_text,
                    ),
                )
        except Exception as exc:
            status = "error"
            error = str(exc)[:1000]
            error_type = type(exc).__name__
            logger.exception(
                "chat_loop.turn_failed",
                iteration=iteration + 1,
                provider=provider,
                model=model,
            )
            final_text = "Ha ocurrido un error al procesar la consulta. Inténtalo de nuevo."
            pending_records = [TurnMessageRecord(role="assistant", content=final_text)]
        finally:
            latency_ms = int((time.perf_counter() - started) * 1000)
            cost = compute_cost_eur(model, input_tokens, output_tokens)
            llm_call = LLMCall(
                tenant_id=tenant_id,
                task="chat",
                model=model,
                provider=provider,
                prompt_version=prompt_version,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_eur=cost,
                latency_ms=latency_ms,
                status=status,
                error=error,
                langfuse_trace_id=trace_id_str,
            )
            db.add(llm_call)
            await db.flush()
            llm_call_ids.append(llm_call.id)
            if ctx.thread_id is not None and tools_this_turn:
                from app.services import audit_service

                for tool_name, tool_result in tools_this_turn:
                    await audit_service.log_chat_tool_executed(
                        db,
                        tenant_id=tenant_id,
                        user_id=ctx.user_id,
                        thread_id=ctx.thread_id,
                        tool_name=tool_name,
                        ok=tool_result.ok,
                        cost_eur=llm_call.cost_eur,
                        llm_call_id=llm_call.id,
                        error=tool_result.error,
                    )
            for record in pending_records:
                if record.role == "assistant" and record.llm_call_id is None:
                    turn_messages.append(
                        TurnMessageRecord(
                            role=record.role,
                            content=record.content,
                            tool_call=record.tool_call,
                            tool_result=record.tool_result,
                            citations=record.citations,
                            llm_call_id=llm_call.id,
                        ),
                    )
                else:
                    turn_messages.append(record)

            obs.update(
                output=trace_text(final_text),
                metadata={
                    "latency_ms": latency_ms,
                    "status": status,
                    "tool_calls": [name for name, _ in tools_this_turn],
                },
                usage_details={"input": input_tokens, "output": output_tokens},
                cost_details={"total": float(cost)},
                level=None if status == "ok" else "ERROR",
                status_message=trace_status_message(error_type=error_type, error=error),
            )
            obs.end()
            langfuse.flush()

        if final_text is not None:
            break

    if final_text is None:
        final_text = _EXHAUSTED_MESSAGE

    final_citations = merge_citation_lists(*citation_batches, settings=settings)
    citations_json = citations_to_json(final_citations) if final_citations else None

    if turn_messages:
        updated: list[TurnMessageRecord] = []
        attached = False
        for record in reversed(turn_messages):
            if (
                not attached
                and record.role == "assistant"
                and record.content
                and record.tool_call is None
            ):
                updated.append(
                    TurnMessageRecord(
                        role=record.role,
                        content=record.content,
                        tool_call=record.tool_call,
                        tool_result=record.tool_result,
                        citations=citations_json,
                        llm_call_id=record.llm_call_id,
                    ),
                )
                attached = True
            else:
                updated.append(record)
        turn_messages = list(reversed(updated))

    rag_obs.update(
        metadata={
            "tenant_id": str(tenant_id),
            "thread_id": str(ctx.thread_id) if ctx.thread_id else None,
            "prompt_version": prompt_version,
            "knowledge_tools_used": knowledge_tools_used,
            "citations_count": len(final_citations),
        },
        output={
            "knowledge_tools_used": knowledge_tools_used,
            "citations_count": len(final_citations),
            "tools_executed": tool_calls_executed,
        },
    )
    rag_obs.end()
    langfuse.flush()

    return ToolLoopResult(
        final_text=final_text,
        llm_call_ids=llm_call_ids,
        tool_calls_executed=tool_calls_executed,
        turn_messages=tuple(turn_messages),
        citations=tuple(final_citations),
        knowledge_tools_used=knowledge_tools_used,
    )


async def _gemini_turn(
    *,
    google_client: genai.Client,
    model: str,
    messages: list[dict[str, Any]],
    registry: ToolRegistry,
) -> _TurnOutcome:
    system_text, gemini_contents = _to_gemini_contents(messages)
    config = genai_types.GenerateContentConfig(
        tools=registry.to_gemini_tools(),
        system_instruction=system_text,
    )
    response = await google_client.aio.models.generate_content(
        model=model,
        contents=cast(Any, gemini_contents),
        config=config,
    )
    input_tokens, output_tokens = _gemini_token_usage(response)
    tool_calls: list[dict[str, Any]] = []
    text_parts: list[str] = []

    candidate = response.candidates[0] if response.candidates else None
    model_content = candidate.content if candidate and candidate.content else None
    parts = list(model_content.parts) if model_content and model_content.parts else []

    for part in parts:
        if part.text:
            text_parts.append(part.text)
        if part.function_call:
            fc = part.function_call
            call: dict[str, Any] = {
                "id": fc.id or str(uuid.uuid4()),
                "name": fc.name or "",
                "arguments": dict(fc.args or {}),
            }
            # Gemini 3 exige reenviar thought_signature en el siguiente turno.
            # Lo serializamos (base64) para historial BD; el Content nativo se
            # guarda aparte para el loop in-memory.
            if part.thought_signature:
                call[_THOUGHT_SIGNATURE_KEY] = base64.b64encode(part.thought_signature).decode(
                    "ascii"
                )
            tool_calls.append(call)

    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_parts) or None,
    }
    if tool_calls:
        assistant_message["tool_calls"] = tool_calls
    # Reutilizar el Content completo de la API (no reconstruir Parts a mano).
    if model_content is not None:
        assistant_message[_GEMINI_MODEL_CONTENT_KEY] = model_content

    return _TurnOutcome(
        assistant_message=assistant_message,
        final_text="".join(text_parts) if text_parts and not tool_calls else None,
        tool_calls=tool_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw=response,
    )


async def _anthropic_turn(
    *,
    anthropic_client: AsyncAnthropic,
    model: str,
    messages: list[dict[str, Any]],
    registry: ToolRegistry,
) -> _TurnOutcome:
    system_text, anthropic_messages = _to_anthropic_messages(messages)
    response = await anthropic_client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_text or "",
        messages=cast(Any, anthropic_messages),
        tools=cast(Any, registry.to_anthropic_tools()),
    )
    input_tokens = int(response.usage.input_tokens)
    output_tokens = int(response.usage.output_tokens)

    tool_calls: list[dict[str, Any]] = []
    text_parts: list[str] = []
    content_blocks: list[dict[str, Any]] = []

    for block in response.content:
        if isinstance(block, TextBlock):
            text_parts.append(block.text)
            content_blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            tool_calls.append(
                {
                    "id": block.id,
                    "name": block.name,
                    "arguments": dict(block.input),
                },
            )
            content_blocks.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                },
            )

    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": content_blocks if tool_calls else "".join(text_parts),
    }
    if tool_calls:
        assistant_message["tool_calls"] = tool_calls

    return _TurnOutcome(
        assistant_message=assistant_message,
        final_text="".join(text_parts) if text_parts and not tool_calls else None,
        tool_calls=tool_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw=response,
    )


def _to_gemini_contents(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[genai_types.Content]]:
    system_parts: list[str] = []
    contents: list[genai_types.Content] = []

    for msg in messages:
        role = msg.get("role")
        if role == "system":
            system_parts.append(str(msg.get("content", "")))
            continue
        if role == "user":
            contents.append(
                genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text=str(msg.get("content", "")))],
                ),
            )
            continue
        if role == "assistant":
            native = msg.get(_GEMINI_MODEL_CONTENT_KEY)
            if isinstance(native, genai_types.Content):
                # Preferir el Content de la API (incluye thought_signature intacto).
                if native.role:
                    contents.append(native)
                else:
                    contents.append(
                        genai_types.Content(role="model", parts=list(native.parts or []))
                    )
                continue
            parts: list[genai_types.Part] = []
            if msg.get("content"):
                parts.append(genai_types.Part(text=str(msg["content"])))
            for call in msg.get("tool_calls", []):
                parts.append(_gemini_function_call_part(call))
            if parts:
                contents.append(genai_types.Content(role="model", parts=parts))
            continue
        if role == "tool":
            payload = msg.get("content", "")
            if isinstance(payload, str):
                try:
                    response_data = json.loads(payload)
                except json.JSONDecodeError:
                    response_data = {"result": payload}
            else:
                response_data = payload
            contents.append(
                genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part(
                            function_response=genai_types.FunctionResponse(
                                id=msg.get("tool_call_id"),
                                name=msg.get("name", ""),
                                response=response_data,
                            ),
                        ),
                    ],
                ),
            )

    return ("\n\n".join(system_parts) if system_parts else None, contents)


def _gemini_function_call_part(call: dict[str, Any]) -> genai_types.Part:
    """Reconstruye un Part de functionCall rehidratando thought_signature si existe."""
    signature = _decode_thought_signature(call.get(_THOUGHT_SIGNATURE_KEY))
    return genai_types.Part(
        function_call=genai_types.FunctionCall(
            id=call.get("id"),
            name=call["name"],
            args=call.get("arguments", {}),
        ),
        thought_signature=signature,
    )


def _decode_thought_signature(raw: object) -> bytes | None:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return base64.b64decode(raw.encode("ascii"), validate=True)
        except (ValueError, TypeError):
            logger.warning("chat_loop.invalid_thought_signature_b64")
            return None
    return None


def _to_anthropic_messages(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    system_parts: list[str] = []
    anthropic_messages: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role")
        if role == "system":
            system_parts.append(str(msg.get("content", "")))
            continue
        if role == "user":
            anthropic_messages.append({"role": "user", "content": str(msg.get("content", ""))})
            continue
        if role == "assistant":
            if msg.get("tool_calls"):
                content: list[dict[str, Any]] = []
                if msg.get("content"):
                    content.append({"type": "text", "text": str(msg["content"])})
                for call in msg["tool_calls"]:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": call["id"],
                            "name": call["name"],
                            "input": call.get("arguments", {}),
                        },
                    )
                anthropic_messages.append({"role": "assistant", "content": content})
            else:
                anthropic_messages.append(
                    {"role": "assistant", "content": str(msg.get("content", ""))},
                )
            continue
        if role == "tool":
            anthropic_messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id"),
                            "content": str(msg.get("content", "")),
                        },
                    ],
                },
            )

    return ("\n\n".join(system_parts) if system_parts else None, anthropic_messages)


def _gemini_token_usage(raw: Any) -> tuple[int, int]:
    um = getattr(raw, "usage_metadata", None)
    if um is None:
        return 0, 0
    return (
        int(getattr(um, "prompt_token_count", None) or 0),
        int(getattr(um, "candidates_token_count", None) or 0),
    )
