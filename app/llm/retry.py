"""Política de reintentos ante errores transitorios de proveedores LLM.

Compartida por ``LLMClient.complete()`` (extracción, clasificación...) y el
loop de tool-calling del chat. Módulo aparte para evitar el import circular
``client`` → ``chat_loop``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = structlog.get_logger(__name__)

# Códigos HTTP retryables: rate-limit y errores de servidor/sobrecarga.
# 529 es específico de Anthropic ("overloaded"); el resto son estándar.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504, 529})

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


def is_retryable_provider_error(exc: BaseException) -> bool:
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
    if isinstance(code, int) and code in RETRYABLE_STATUS:
        return True

    # Fallback: algunos SDKs envuelven el error en una excepción genérica con
    # el código embebido en el mensaje. Es una red de seguridad, no la vía principal.
    msg = str(exc).lower()
    return any(marker in msg for marker in _RETRYABLE_MARKERS)


def log_transient_retry(
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


async def call_with_transient_retry[T](
    call: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    max_wait_seconds: float,
    provider: str,
    model: str,
) -> T:
    """Ejecuta ``call`` reintentando solo errores transitorios del proveedor.

    Backoff exponencial desde 1 s con jitter, hasta ``max_wait_seconds`` por
    espera. Tras agotar intentos relanza la excepción original (no RetryError).
    """
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(max(1, max_attempts)),
        wait=wait_exponential_jitter(initial=1.0, max=max_wait_seconds),
        retry=retry_if_exception(is_retryable_provider_error),
        before_sleep=lambda rs: log_transient_retry(rs, provider=provider, model=model),
        reraise=True,
    ):
        with attempt:
            return await call()
    # Unreachable: con reraise=True se devuelve dentro del `with` o se relanza.
    raise RuntimeError("AsyncRetrying exited without yielding a result")
