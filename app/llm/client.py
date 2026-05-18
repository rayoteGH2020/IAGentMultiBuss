"""Cliente LLM unificado: Anthropic vs Google según modelo, auditoría en `llm_calls`, Langfuse."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

import instructor
import structlog
from anthropic import AsyncAnthropic
from google import genai
from langfuse.types import TraceContext
from pydantic import BaseModel

from app.config import get_settings
from app.llm.pricing import compute_cost_eur
from app.llm.tracing import get_langfuse
from app.models import LLMCall

if TYPE_CHECKING:
    from uuid import UUID

    from openai.types.chat import ChatCompletionMessageParam
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

TaskType = Literal["extraction", "chat", "sql", "classify"]

DEFAULT_MODELS: dict[TaskType, str] = {
    "extraction": "gemini-2.5-flash",
    "classify": "claude-haiku-4-5-20251001",
    "chat": "claude-sonnet-4-6",
    "sql": "claude-sonnet-4-6",
}

T = TypeVar("T", bound=BaseModel)


def _extract_token_usage(raw: Any) -> tuple[int, int]:
    """Obtiene tokens desde la respuesta cruda del SDK (Anthropic o GenAI)."""
    if raw is None:
        return 0, 0
    usage = getattr(raw, "usage", None)
    if usage is not None:
        in_t = getattr(usage, "input_tokens", None)
        out_t = getattr(usage, "output_tokens", None)
        if in_t is not None or out_t is not None:
            return int(in_t or 0), int(out_t or 0)
        p_t = getattr(usage, "prompt_tokens", None)
        c_t = getattr(usage, "completion_tokens", None)
        if p_t is not None or c_t is not None:
            return int(p_t or 0), int(c_t or 0)

    um = getattr(raw, "usage_metadata", None)
    if um is not None:
        return int(getattr(um, "prompt_token_count", None) or 0), int(
            getattr(um, "candidates_token_count", None) or 0
        )

    return 0, 0


class LLMClient:
    """Punto de entrada para completion estructurado vía Instructor."""

    def __init__(self) -> None:
        self._settings = get_settings()
        sk_anthropic = self._settings.anthropic_api_key.get_secret_value()
        sk_google = self._settings.google_api_key.get_secret_value()
        self._anthropic = instructor.from_anthropic(
            AsyncAnthropic(api_key=sk_anthropic or "missing-anthropic-key"),
        )
        self._google = instructor.from_genai(
            genai.Client(api_key=sk_google or "missing-google-key"),
            use_async=True,
        )
        self._langfuse = get_langfuse()

    def _resolve_model(self, task: TaskType) -> tuple[str, str]:
        overrides: dict[TaskType, str | None] = {
            "extraction": self._settings.llm_model_extraction,
            "chat": self._settings.llm_model_chat,
            "classify": self._settings.llm_model_classify,
            "sql": self._settings.llm_model_sql,
        }
        model = overrides[task] or DEFAULT_MODELS[task]
        if model.startswith("claude"):
            return model, "anthropic"
        return model, "google"

    async def complete(
        self,
        *,
        task: TaskType,
        messages: list[dict[str, Any]],
        response_model: type[T],
        tenant_id: UUID,
        db: AsyncSession,
        prompt_version: str | None = None,
        max_retries: int = 2,
    ) -> T:
        model, provider = self._resolve_model(task)

        trace_uuid = self._langfuse.create_trace_id()
        trace_id_str = str(trace_uuid)
        trace_ctx = TraceContext(trace_id=trace_id_str)

        obs = self._langfuse.start_observation(
            trace_context=trace_ctx,
            name=f"llm.{task}",
            as_type="generation",
            metadata={
                "tenant_id": str(tenant_id),
                "prompt_version": prompt_version,
                "provider": provider,
            },
            model=model,
            input=messages,
        )

        typed_messages = cast("list[ChatCompletionMessageParam]", messages)

        started = time.perf_counter()
        status = "ok"
        error: str | None = None
        input_tokens = 0
        output_tokens = 0
        result: T | None = None
        raw: Any = None

        try:
            if provider == "anthropic":
                result, raw = await self._anthropic.messages.create_with_completion(
                    model=model,
                    messages=typed_messages,
                    response_model=response_model,
                    max_retries=max_retries,
                    max_tokens=4096,
                )
            else:
                result, raw = await self._google.chat.completions.create_with_completion(
                    model=model,
                    messages=typed_messages,
                    response_model=response_model,
                    max_retries=max_retries,
                )
            input_tokens, output_tokens = _extract_token_usage(raw)
        except Exception as exc:
            status = "error"
            error = str(exc)[:1000]
            logger.exception(
                "llm.complete_failed",
                task=task,
                model=model,
                tenant_id=str(tenant_id),
                error=error,
            )
            raise
        finally:
            latency_ms = int((time.perf_counter() - started) * 1000)
            cost = compute_cost_eur(model, input_tokens, output_tokens)

            db.add(
                LLMCall(
                    tenant_id=tenant_id,
                    task=task,
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
                ),
            )

            obs.update(
                output=result.model_dump() if result is not None else None,
                metadata={
                    "latency_ms": latency_ms,
                    "status": status,
                },
                usage_details={"input": input_tokens, "output": output_tokens},
                cost_details={"total": float(cost)},
                level=None if status == "ok" else "ERROR",
                status_message=error,
            )
            obs.end()
            self._langfuse.flush()

        assert result is not None
        return result


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Singleton perezoso para reutilizar clientes HTTP de proveedor."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def reset_llm_client_for_tests() -> None:
    """Limpia singleton (solo tests)."""
    global _client
    _client = None
