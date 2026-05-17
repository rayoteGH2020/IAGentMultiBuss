# Paso 10 — Cliente LLM unificado, tabla `llm_calls`, prompts versionados y Langfuse

## Objetivo

Montar la capa LLM del proyecto: cliente único que enruta a Anthropic o Google según la tarea, prompts versionados como ficheros, tabla `llm_calls` para auditoría/coste, y trazado en Langfuse. Al final del paso, una llamada de prueba (devolviendo un Pydantic) funciona, queda registrada en `llm_calls` y es visible en Langfuse.

Este paso no toca facturas todavía. Construye la base sobre la que Paso 12 implementará la extracción real.

## Pre-requisitos

- Pasos 01-09 completados.
- Claves de API: `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`.
- Langfuse local levantado (Paso 02) con sus claves.

## Contexto relevante

- `arquitectura.md` sección 8 (Capa LLM): router por defecto, prompts versionados, observabilidad.
- `Agents.md`: no LangChain, prompts en ficheros con sufijo `_vN`, cada llamada en `llm_calls` + Langfuse, Instructor para output estructurado.

## Tareas

- [ ] Añadir dependencias: `anthropic`, `google-genai`, `instructor`, `langfuse`.
- [ ] Añadir variables a `app/config.py` y a `.env.example`.
- [ ] Crear `app/models/llm_call.py`.
- [ ] Exportar el modelo desde `app/models/__init__.py`.
- [ ] Migración Alembic con RLS para `llm_calls`.
- [ ] Crear carpeta `app/llm/prompts/` con `ping_v1.txt`.
- [ ] Crear `app/llm/prompts_loader.py` con `load_prompt` y `render_prompt`.
- [ ] Crear `app/llm/pricing.py`.
- [ ] Crear `app/llm/tracing.py`.
- [ ] Crear `app/llm/client.py` con `LLMClient` y router por tarea.
- [ ] Crear `app/llm/__init__.py`.
- [ ] Test de humo en `tests/integration/test_llm_client.py`.
- [ ] Verificar traza en `http://localhost:3000` (Langfuse).
- [ ] Commit: `feat: llm client with provider routing, versioned prompts, langfuse tracing`.

## Detalles técnicos

### `app/config.py` (añadir)

```python
class Settings(BaseSettings):
    # ... lo anterior ...
    anthropic_api_key: str
    google_api_key: str
    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_host: str = "http://localhost:3000"
    llm_model_extraction: str | None = None
    llm_model_chat: str | None = None
    llm_model_classify: str | None = None
    llm_model_sql: str | None = None
```

### `app/models/llm_call.py`

```python
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LLMCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    task: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)

    input_tokens: Mapped[int] = mapped_column(default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(default=0, nullable=False)
    cost_eur: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), default=Decimal("0"), nullable=False
    )
    latency_ms: Mapped[int] = mapped_column(default=0, nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ok")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_llm_calls_tenant_created", "tenant_id", "created_at"),
    )
```

Genera migración con `uv run alembic revision --autogenerate -m "add llm_calls"` y añade RLS como en Paso 09.

### `app/llm/prompts/ping_v1.txt`

```
Eres un asistente conciso. Devuelve un saludo personalizado en español.
Nombre del destinatario: {name}
```

### `app/llm/prompts_loader.py`

```python
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=128)
def load_prompt(name: str) -> str:
    """Carga un prompt versionado desde app/llm/prompts/{name}.txt.

    Convención: nombre incluye sufijo _vN. Nunca editar in-place: crear _v2.
    """
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def render_prompt(name: str, **kwargs: object) -> str:
    return load_prompt(name).format(**kwargs)
```

### `app/llm/pricing.py`

```python
from decimal import Decimal

# Precios en EUR por 1M tokens. Revisar trimestralmente.
PRICING: dict[str, dict[str, Decimal]] = {
    "claude-haiku-4-5-20251001": {"input": Decimal("0.90"), "output": Decimal("4.50")},
    "claude-sonnet-4-6": {"input": Decimal("2.80"), "output": Decimal("14.00")},
    "gemini-2.5-flash": {"input": Decimal("0.28"), "output": Decimal("2.30")},
    "gemini-2.5-pro": {"input": Decimal("1.10"), "output": Decimal("4.40")},
    "voyage-3-lite": {"input": Decimal("0.018"), "output": Decimal("0")},
}


def compute_cost_eur(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    rates = PRICING.get(model)
    if rates is None:
        return Decimal("0")
    cost = (
        Decimal(input_tokens) * rates["input"]
        + Decimal(output_tokens) * rates["output"]
    ) / Decimal("1000000")
    return cost.quantize(Decimal("0.000001"))
```

### `app/llm/tracing.py`

```python
from functools import lru_cache

from langfuse import Langfuse

from app.config import settings


@lru_cache(maxsize=1)
def get_langfuse() -> Langfuse:
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
```

### `app/llm/client.py`

```python
from __future__ import annotations

import time
import uuid
from typing import Literal, TypeVar

import instructor
import structlog
from anthropic import AsyncAnthropic
from google import genai
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.llm.pricing import compute_cost_eur
from app.llm.tracing import get_langfuse
from app.models import LLMCall

logger = structlog.get_logger(__name__)

TaskType = Literal["extraction", "chat", "sql", "classify"]

DEFAULT_MODELS: dict[TaskType, str] = {
    "extraction": "gemini-2.5-flash",
    "classify": "claude-haiku-4-5-20251001",
    "chat": "claude-sonnet-4-6",
    "sql": "claude-sonnet-4-6",
}

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    def __init__(self) -> None:
        self._anthropic = instructor.from_anthropic(
            AsyncAnthropic(api_key=settings.anthropic_api_key)
        )
        self._google = instructor.from_genai(
            genai.Client(api_key=settings.google_api_key), use_async=True
        )
        self._langfuse = get_langfuse()

    def _resolve_model(self, task: TaskType) -> tuple[str, str]:
        override = getattr(settings, f"llm_model_{task}", None)
        model = override or DEFAULT_MODELS[task]
        provider = "anthropic" if model.startswith("claude") else "google"
        return model, provider

    async def complete(
        self,
        *,
        task: TaskType,
        messages: list[dict],
        response_model: type[T],
        tenant_id: uuid.UUID,
        db: AsyncSession,
        prompt_version: str | None = None,
        max_retries: int = 2,
    ) -> T:
        model, provider = self._resolve_model(task)
        trace = self._langfuse.trace(
            name=f"llm.{task}",
            metadata={"tenant_id": str(tenant_id), "model": model},
        )
        start = time.perf_counter()
        status = "ok"
        error: str | None = None
        input_tokens = output_tokens = 0
        result: T | None = None

        try:
            if provider == "anthropic":
                result, raw = await self._anthropic.messages.create_with_completion(
                    model=model,
                    messages=messages,
                    response_model=response_model,
                    max_retries=max_retries,
                    max_tokens=4096,
                )
                input_tokens = raw.usage.input_tokens
                output_tokens = raw.usage.output_tokens
            else:
                result = await self._google.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_model=response_model,
                    max_retries=max_retries,
                )
                input_tokens = getattr(result, "_input_tokens", 0)
                output_tokens = getattr(result, "_output_tokens", 0)
        except Exception as exc:
            status = "error"
            error = str(exc)[:1000]
            logger.exception("llm.error", task=task, model=model)
            raise
        finally:
            latency_ms = int((time.perf_counter() - start) * 1000)
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
                    langfuse_trace_id=trace.id,
                )
            )
            trace.update(
                output=result.model_dump() if result else None,
                metadata={
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_eur": float(cost),
                    "latency_ms": latency_ms,
                    "status": status,
                },
            )
            self._langfuse.flush()

        assert result is not None
        return result


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
```

### `app/llm/__init__.py`

```python
from app.llm.client import LLMClient, get_llm_client
from app.llm.prompts_loader import load_prompt, render_prompt

__all__ = ["LLMClient", "get_llm_client", "load_prompt", "render_prompt"]
```

### Test de humo

`tests/integration/test_llm_client.py`:

```python
import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.llm import get_llm_client, render_prompt
from app.models import LLMCall


class Greeting(BaseModel):
    saludo: str
    idioma: str


@pytest.mark.asyncio
@pytest.mark.integration
async def test_llm_client_classify_smoke(db_session, tenant_factory):
    tenant = await tenant_factory()
    client = get_llm_client()

    prompt = render_prompt("ping_v1", name="Ana")
    result = await client.complete(
        task="classify",
        messages=[{"role": "user", "content": prompt}],
        response_model=Greeting,
        tenant_id=tenant.id,
        prompt_version="ping_v1",
        db=db_session,
    )
    await db_session.commit()

    assert result.saludo
    assert result.idioma

    rows = (await db_session.execute(select(LLMCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "ok"
    assert rows[0].input_tokens > 0
```

## Criterios de aceptación

- `uv run alembic upgrade head` aplica la migración.
- El test de humo pasa contra la API real.
- En Langfuse aparece una traza `llm.classify` con tokens y coste.
- Hay una fila en `llm_calls` con `status='ok'`, `cost_eur > 0`.

## Lo que NO toca este paso

- Lógica de extracción de facturas (Paso 12).
- Embeddings y RAG (módulo 2).
- Streaming SSE.
- Tool use / function calling.

## Posibles problemas

- **`instructor.from_genai` API distinta**: ajusta a la versión instalada (puede ser `instructor.from_google` o `instructor.patch`).
- **Conteo de tokens en Google**: si el SDK no los expone, acepta `0` y prioriza la traza de Langfuse.
- **Langfuse local lento al primer arranque**: espera 30-60s tras `docker compose up`.

## Siguiente paso

`Paso11.md` — Cliente Storage R2 (Cloudflare) con presigned URLs para subir y descargar archivos.
