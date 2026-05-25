"""Cliente LLM unificado: Anthropic vs Google según modelo, auditoría en `llm_calls`, Langfuse.

Punto de entrada único para toda llamada LLM de la aplicación (arquitectura.md §8).
Ni services ni routes llaman directamente a los SDKs de Anthropic o Google;
todo pasa por LLMClient.complete() para garantizar trazado, coste y auditoría
consistentes en todas las llamadas.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

if TYPE_CHECKING:
    from uuid import UUID

import instructor
import structlog
from anthropic import AsyncAnthropic
from google import genai
from langfuse.types import TraceContext
from pydantic import BaseModel
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.config import Settings, get_settings
from app.core.errors import LLMCompleteError
from app.llm.chat_loop import ToolLoopResult
from app.llm.chat_loop import run_tool_loop as _run_tool_loop
from app.llm.pricing import compute_cost_eur
from app.llm.tools.registry import ToolContext, ToolRegistry
from app.llm.tracing import get_langfuse
from app.models import LLMCall

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

_MISSING_ANTHROPIC_KEY_PLACEHOLDER = "missing-anthropic-key"
_ANTHROPIC_TASKS = frozenset({"classify", "sql"})

# Literal restringe los valores en tiempo de type-check; un typo en task sería
# detectado por mypy antes de llegar a DEFAULT_MODELS en runtime.
TaskType = Literal["extraction", "chat", "sql", "classify"]

# Códigos HTTP retryables: rate-limit y errores de servidor/sobrecarga.
# 529 es específico de Anthropic ("overloaded"); el resto son estándar.
# frozenset: inmutable y O(1) en lookup.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504, 529})

# Markers textuales para fallback cuando la excepción del SDK no expone .code
# de forma estructurada. Se buscan en minúsculas en str(exc).
_RETRYABLE_MARKERS: tuple[str, ...] = (
    "503",
    "504",
    "529",
    "unavailable",
    "overloaded",
    "rate limit",
    "rate_limit_error",
    "too many requests",
    "internal server error",
)


def _is_retryable_provider_error(exc: BaseException) -> bool:
    """True si la excepción del SDK indica un fallo transitorio del proveedor.

    No reintentamos ValidationError de Pydantic (lo hace Instructor) ni 4xx
    distintos de 429 (un 400 / 401 / 403 no se arregla reintentando, sería
    coste sin sentido).
    """
    # Distintos SDKs exponen el código HTTP con nombres distintos:
    # - google-genai: ApiError.code
    # - anthropic: APIStatusError.status_code
    # - httpx en bruto: HTTPStatusError.response.status_code
    code = getattr(exc, "code", None)
    if not isinstance(code, int):
        code = getattr(exc, "status_code", None)
    if not isinstance(code, int):
        response = getattr(exc, "response", None)
        code = getattr(response, "status_code", None) if response is not None else None
    if isinstance(code, int) and code in _RETRYABLE_STATUS:
        return True

    # Fallback: algunos SDKs envuelven el error en una excepción genérica con
    # el código embebido en el mensaje. Es una red de seguridad, no la vía principal.
    msg = str(exc).lower()
    return any(marker in msg for marker in _RETRYABLE_MARKERS)


def _anthropic_api_key_configured(settings: Settings) -> bool:
    raw = settings.anthropic_api_key.get_secret_value().strip()
    return bool(raw) and raw != _MISSING_ANTHROPIC_KEY_PLACEHOLDER


def _log_anthropic_failure(
    *,
    event: str,
    task: TaskType,
    model: str,
    tenant_id: str,
    error: str,
    exc: BaseException | None,
) -> None:
    """Log de error Anthropic muy visible en consola (classify / sql / chat con Claude)."""
    log = logger.bind(
        provider="anthropic",
        task=task,
        model=model,
        tenant_id=tenant_id,
        error=error,
        hint=(
            "Revisa ANTHROPIC_API_KEY en Infisical. "
            "classify y sql usan Claude por defecto; chat usa Gemini salvo LLM_MODEL_CHAT."
        ),
    )
    if exc is not None:
        log.error(event, exc_type=type(exc).__name__, exc_info=exc)
    else:
        log.error(event)


def _log_transient_retry(
    retry_state: RetryCallState,
    *,
    provider: str,
    model: str,
) -> None:
    """Callback de tenacity: loguea reintentos; Anthropic con evento dedicado."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    next_sleep = getattr(retry_state.next_action, "sleep", None)
    payload = {
        "provider": provider,
        "model": model,
        "attempt": retry_state.attempt_number,
        "next_sleep_s": next_sleep,
        "error": str(exc)[:200] if exc else None,
    }
    if provider == "anthropic":
        logger.warning("anthropic_llm_retry", **payload)
    else:
        logger.warning("llm.retry_transient_error", **payload)


# Router de modelos por defecto (arquitectura.md §8). Se puede sobreescribir
# por entorno via LLM_MODEL_* en config.py sin tocar código.
# - extraction / chat: Gemini Flash (GOOGLE_API_KEY); mismo proveedor que extracción.
# - classify: Haiku es el modelo más económico de Anthropic para tareas simples.
# - sql: Sonnet (ANTHROPIC_API_KEY) cuando exista módulo analytics.
DEFAULT_MODELS: dict[TaskType, str] = {
    "extraction": "gemini-2.5-flash",
    "classify": "claude-haiku-4-5-20251001",
    "chat": "gemini-2.5-flash",
    # "chat": "claude-sonnet-4-6",  # alternativa Anthropic; requiere ANTHROPIC_API_KEY
    "sql": "claude-sonnet-4-6",
}

# TypeVar acotado a BaseModel: permite que complete() sea genérico y devuelva
# el tipo concreto (Factura, etc.) en lugar de BaseModel, preservando el tipo
# en el callsite sin necesidad de cast externo.
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class LLMCompleteResult[T: BaseModel]:
    """Resultado estructurado de una llamada LLM auditada en `llm_calls`."""

    result: T
    llm_call_id: UUID


def _extract_token_usage(raw: Any) -> tuple[int, int]:
    """Obtiene tokens desde la respuesta cruda del SDK (Anthropic o GenAI).

    Cada SDK (y cada versión) expone los conteos de tokens con nombres distintos.
    Esta función normaliza los tres formatos conocidos sin fallar ante respuestas
    inesperadas, devolviendo (0, 0) si no puede extraer los valores.
    """
    if raw is None:
        return 0, 0
    usage = getattr(raw, "usage", None)
    if usage is not None:
        # Formato Anthropic: input_tokens / output_tokens
        in_t = getattr(usage, "input_tokens", None)
        out_t = getattr(usage, "output_tokens", None)
        if in_t is not None or out_t is not None:
            return int(in_t or 0), int(out_t or 0)
        # Formato OpenAI-compatible (Instructor puede wrappear así): prompt_tokens / completion_tokens
        p_t = getattr(usage, "prompt_tokens", None)
        c_t = getattr(usage, "completion_tokens", None)
        if p_t is not None or c_t is not None:
            return int(p_t or 0), int(c_t or 0)

    # Formato Google Gemini: usage_metadata con prompt_token_count / candidates_token_count
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
        # Placeholder "missing-*-key": permite que el cliente se instancie aunque
        # la API key no esté configurada (tests unitarios, arranque sin módulo LLM).
        # La llamada real fallará con error de autenticación, no en el constructor.
        self._anthropic_raw = AsyncAnthropic(
            api_key=sk_anthropic or _MISSING_ANTHROPIC_KEY_PLACEHOLDER,
        )
        self._anthropic = instructor.from_anthropic(self._anthropic_raw)
        # use_async=True: la librería google-genai necesita este flag explícito
        # para devolver coroutines en lugar de resultados síncronos.
        self._google_raw = genai.Client(api_key=sk_google or "missing-google-key")
        self._google = instructor.from_genai(
            self._google_raw,
            use_async=True,
        )
        self._langfuse = get_langfuse()
        if not _anthropic_api_key_configured(self._settings):
            logger.warning(
                "llm.anthropic_not_configured",
                affected_tasks=sorted(_ANTHROPIC_TASKS),
                default_models={
                    "classify": DEFAULT_MODELS["classify"],
                    "sql": DEFAULT_MODELS["sql"],
                },
                hint=(
                    "Sin ANTHROPIC_API_KEY las tareas classify/sql fallarán con "
                    "anthropic_api_key_missing en consola."
                ),
            )

    def _resolve_model(self, task: TaskType) -> tuple[str, str]:
        # Overrides de entorno tienen prioridad sobre DEFAULT_MODELS.
        # Devuelve (model_id, provider) para que complete() sepa qué SDK usar.
        overrides: dict[TaskType, str | None] = {
            "extraction": self._settings.llm_model_extraction,
            "chat": self._settings.llm_model_chat,
            "classify": self._settings.llm_model_classify,
            "sql": self._settings.llm_model_sql,
        }
        model = overrides[task] or DEFAULT_MODELS[task]
        # Heurística de routing: todos los modelos Claude empiezan por "claude";
        # el resto (gemini-*, voyage-*) van al proveedor Google.
        if model.startswith("claude"):
            return model, "anthropic"
        return model, "google"

    async def _call_sdk_once(
        self,
        *,
        provider: str,
        model: str,
        typed_messages: list[ChatCompletionMessageParam],
        response_model: type[T],
        max_retries: int,
    ) -> tuple[T, Any]:
        """Una sola llamada al SDK del proveedor. Sin reintentos de transporte.

        `max_retries` se propaga a Instructor para los reintentos de validación
        de schema (independientes de los reintentos por error HTTP que aplica
        _invoke_sdk si el flag está activo).
        """
        if provider == "anthropic":
            # create_with_completion: método de Instructor que devuelve
            # (objeto_pydantic, respuesta_cruda_sdk). La respuesta cruda es
            # necesaria para extraer los conteos de tokens.
            # max_tokens=4096: Anthropic requiere este parámetro explícito para
            # evitar respuestas truncadas; Google gestiona el límite internamente.
            return cast(
                tuple[T, Any],
                await self._anthropic.messages.create_with_completion(
                    model=model,
                    messages=typed_messages,
                    response_model=response_model,
                    max_retries=max_retries,
                    max_tokens=4096,
                ),
            )
        return cast(
            tuple[T, Any],
            await self._google.chat.completions.create_with_completion(
                model=model,
                messages=typed_messages,
                response_model=response_model,
                max_retries=max_retries,
            ),
        )

    async def _invoke_sdk(
        self,
        *,
        provider: str,
        model: str,
        typed_messages: list[ChatCompletionMessageParam],
        response_model: type[T],
        max_retries: int,
    ) -> tuple[T, Any]:
        """Invoca el SDK, opcionalmente con reintentos para errores transitorios.

        Si `settings.llm_retry_transient_errors` está activo, envuelve la
        llamada en `AsyncRetrying` con backoff exponencial + jitter para
        capturar fallos HTTP 429/5xx/529 del proveedor. Si no, comportamiento
        equivalente al cliente original (una sola llamada).
        """
        if not self._settings.llm_retry_transient_errors:
            return await self._call_sdk_once(
                provider=provider,
                model=model,
                typed_messages=typed_messages,
                response_model=response_model,
                max_retries=max_retries,
            )

        # reraise=True: tras agotar reintentos, vuelve a lanzar la excepción
        # original tal cual, no la RetryError de tenacity. Mantiene la semántica
        # del except externo en complete() (que ya sabe formatear errores SDK).
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._settings.llm_retry_max_attempts),
            wait=wait_exponential_jitter(
                initial=1.0,
                max=self._settings.llm_retry_max_wait_seconds,
            ),
            retry=retry_if_exception(_is_retryable_provider_error),
            before_sleep=lambda rs: _log_transient_retry(rs, provider=provider, model=model),
            reraise=True,
        ):
            with attempt:
                return await self._call_sdk_once(
                    provider=provider,
                    model=model,
                    typed_messages=typed_messages,
                    response_model=response_model,
                    max_retries=max_retries,
                )
        # Unreachable: AsyncRetrying con reraise=True siempre devuelve dentro
        # del `with attempt:` o re-lanza la excepción del último intento.
        # Necesario para que mypy no se queje de "missing return".
        raise RuntimeError("AsyncRetrying exited without yielding a result")

    async def complete(
        self,
        *,  # Todos keyword-only: evita confusión de orden en una firma larga.
        task: TaskType,
        messages: list[dict[str, Any]],
        response_model: type[T],
        tenant_id: UUID,
        db: AsyncSession,
        prompt_version: str | None = None,
        max_retries: int = 2,
    ) -> LLMCompleteResult[T]:
        model, provider = self._resolve_model(task)

        # El trace_id se crea ANTES de la llamada al LLM para que quede
        # registrado en LLMCall incluso si la llamada falla. Así cada error
        # en BD tiene su traza correspondiente en Langfuse para diagnóstico.
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

        # cast solo informa a mypy del tipo esperado; no hace conversión en runtime.
        # Ambos SDKs aceptan el formato de mensajes OpenAI-compatible vía Instructor.
        typed_messages = cast("list[ChatCompletionMessageParam]", messages)

        started = time.perf_counter()
        # status y error se inicializan aquí y se modifican en try/except;
        # el bloque finally los lee para persistir el LLMCall en cualquier caso.
        status = "ok"
        error: str | None = None
        input_tokens = 0
        output_tokens = 0
        result: T | None = None
        raw: Any = None
        llm_call: LLMCall | None = None

        try:
            anthropic_key_missing = provider == "anthropic" and not _anthropic_api_key_configured(
                self._settings
            )
            if anthropic_key_missing:
                status = "error"
                error = (
                    f"ANTHROPIC_API_KEY is not configured (task={task}, model={model}). "
                    "Set ANTHROPIC_API_KEY in Infisical or override LLM_MODEL_CLASSIFY / "
                    "LLM_MODEL_SQL with a Gemini model."
                )
                _log_anthropic_failure(
                    event="anthropic_api_key_missing",
                    task=task,
                    model=model,
                    tenant_id=str(tenant_id),
                    error=error,
                    exc=None,
                )
            else:
                try:
                    result, raw = await self._invoke_sdk(
                        provider=provider,
                        model=model,
                        typed_messages=typed_messages,
                        response_model=response_model,
                        max_retries=max_retries,
                    )
                    input_tokens, output_tokens = _extract_token_usage(raw)
                except Exception as exc:
                    status = "error"
                    # Truncado a 1000 chars: el error puede contener la respuesta
                    # completa del LLM si Instructor falla al parsear el schema.
                    error = str(exc)[:1000]
                    if provider == "anthropic":
                        _log_anthropic_failure(
                            event="anthropic_llm_call_failed",
                            task=task,
                            model=model,
                            tenant_id=str(tenant_id),
                            error=error,
                            exc=exc,
                        )
                    else:
                        logger.exception(
                            "llm.complete_failed",
                            task=task,
                            model=model,
                            provider=provider,
                            tenant_id=str(tenant_id),
                            error=error,
                        )
        finally:
            # El bloque finally se ejecuta SIEMPRE: en éxito y en error.
            # Garantiza que LLMCall y la traza de Langfuse se cierran aunque
            # la llamada LLM haya fallado. Sin esto perderíamos métricas de
            # coste y latencia de los errores.
            latency_ms = int((time.perf_counter() - started) * 1000)
            cost = compute_cost_eur(model, input_tokens, output_tokens)

            llm_call = LLMCall(
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
            )
            db.add(llm_call)
            await db.flush()

            # update() + end() + flush(): secuencia Langfuse para cerrar el span
            # con los datos de resultado y enviarlo al servidor. flush() fuerza
            # el envío inmediato; sin él los datos podrían perderse si el proceso
            # termina antes del siguiente ciclo de envío en background.
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

        assert llm_call is not None
        if status == "error":
            raise LLMCompleteError(error or "LLM call failed", llm_call_id=llm_call.id)

        assert result is not None
        return LLMCompleteResult(result=result, llm_call_id=llm_call.id)

    async def run_tool_loop(
        self,
        *,
        messages: list[dict[str, Any]],
        registry: ToolRegistry,
        ctx: ToolContext,
        tenant_id: UUID,
        db: AsyncSession,
        prompt_version: str,
        max_iters: int = 6,
        task: TaskType = "chat",
    ) -> ToolLoopResult:
        """Loop de tool-calling para consulta documental (auditoría en ``llm_calls``)."""
        if task != "chat":
            msg = f"run_tool_loop only supports task='chat', got {task!r}"
            raise ValueError(msg)
        model, provider = self._resolve_model("chat")
        return await _run_tool_loop(
            provider=provider,
            model=model,
            messages=messages,
            registry=registry,
            ctx=ctx,
            tenant_id=tenant_id,
            db=db,
            prompt_version=prompt_version,
            max_iters=max_iters,
            settings=self._settings,
            anthropic_client=self._anthropic_raw,
            google_client=self._google_raw,
        )


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Singleton perezoso para reutilizar clientes HTTP de proveedor.

    Los clientes de Anthropic y Google mantienen conexiones HTTP persistentes
    (connection pool); crear una instancia por request sería costoso e innecesario.
    """
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def reset_llm_client_for_tests() -> None:
    """Limpia el singleton para que los tests puedan inyectar mocks frescos."""
    global _client
    _client = None


__all__ = [
    "DEFAULT_MODELS",
    "LLMClient",
    "LLMCompleteResult",
    "TaskType",
    "ToolLoopResult",
    "get_llm_client",
    "reset_llm_client_for_tests",
]
