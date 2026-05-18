# Paso 10 — Cliente LLM unificado, tabla `llm_calls`, prompts versionados y Langfuse

## Objetivo

Montar la capa LLM del proyecto: cliente único que enruta a Anthropic o Google según la tarea, prompts versionados como ficheros, tabla `llm_calls` para auditoría/coste, y trazado en Langfuse. Al final del paso, una llamada de prueba (devolviendo un Pydantic) funciona, queda registrada en `llm_calls` y es visible en Langfuse.

Este paso no toca facturas todavía. Construye la base sobre la que Paso 12 implementará la extracción real.

## Pre-requisitos

- Pasos 01-09 completados.
- Claves de API: `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`.
- Langfuse local levantado (Paso 02) con sus claves (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`).
- Variables y secretos inyectados con **Infisical** (`infisical run -- uv run alembic upgrade head`, `infisical run -- uv run pytest ...`). Este proyecto **no** usa ficheros `.env`; ver `Agents.md` (gestión de secretos).

## Contexto relevante

- `arquitectura.md` sección 8 (Capa LLM): router por defecto, prompts versionados, observabilidad.
- `Agents.md`: no LangChain, prompts en ficheros con sufijo `_vN`, cada llamada en `llm_calls` + Langfuse, Instructor para output estructurado.

## Secretos (Infisical)

Definir en Infisical (o en el entorno que el CLI exporte), entre otros: `APP_SECRET_KEY`, `DATABASE_URL`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`. Ejecutar la app y las migraciones con `infisical run -- <comando>`.

## Tareas

- [ ] Añadir dependencias: `anthropic`, `google-genai`, `instructor`, `langfuse`.
- [ ] Añadir variables a `app/config.py` (valores vía entorno / Infisical).
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

Expón en `Settings` (`pydantic-settings`, `env_file=None`) lectura desde Infisical/entorno, por ejemplo:

- `anthropic_api_key`, `google_api_key`, `voyage_api_key` como `SecretStr` (pueden estar vacíos donde no aplique).
- `langfuse_public_key`, `langfuse_secret_key`, `langfuse_host`.
- Overrides opcionales: `llm_model_extraction`, `llm_model_chat`, `llm_model_classify`, `llm_model_sql`.

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

Revisión `p10_llm_calls_01`: crear tabla `llm_calls`, política `tenant_isolation`, `FORCE ROW LEVEL SECURITY`, `GRANT` al rol `saas_app` igual que invoices (Paso 09). Opcional: `uv run alembic revision --autogenerate` y revisar antes de aplicar.

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

Factory `get_langfuse()` usando `from app.config import get_settings`, `public_key` / `secret_key` opcionales (strings vacíos se normalizan a `None`; sin claves válidas Langfuse trabaja deshabilitado). Host por defecto `http://localhost:3000`.

### `app/llm/client.py`

Implementación en repo:

- Singleton `get_llm_client()` y clase `LLMClient` con `complete(...)` async.
- `DEFAULT_MODELS` por `TaskType` y overrides desde `Settings`.
- Proveedor `anthropic` si el nombre del modelo empieza por `claude`, en caso contrario `google` (`gemini-...`).
- Anthropic: `instructor.from_anthropic(AsyncAnthropic(...))` y `messages.create_with_completion`.
- Google: `instructor.from_genai(Client(...), use_async=True)` y `chat.completions.create_with_completion`.
- **Langfuse 2.x**: `create_trace_id()`, `start_observation(..., as_type="generation", trace_context=TraceContext(...))`, `update` con `usage_details` / `cost_details`, `end()`, `flush()`.
- Tras cada intento: fila en `llm_calls` con `langfuse_trace_id` (id de traza), coste vía `compute_cost_eur`.

### `app/llm/__init__.py`

```python
from app.llm.client import LLMClient, get_llm_client
from app.llm.prompts_loader import load_prompt, render_prompt

__all__ = ["LLMClient", "get_llm_client", "load_prompt", "render_prompt"]
```

### Test de humo

`tests/integration/test_llm_client.py`: skip si falta tabla `llm_calls` (migración pendiente). Skip si falta `ANTHROPIC_API_KEY` salvo ejecutar con `infisical run -- pytest ...`.

Antes de `complete` / `SELECT`, llama `await set_tenant_context(db_session, str(tenant.id))` (`app.core.db`) para cumplir RLS.

Ejemplo:

```python
import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.config import get_settings
from app.core.db import set_tenant_context
from app.llm import get_llm_client, render_prompt
from app.llm.client import reset_llm_client_for_tests
from app.models import LLMCall

pytestmark = pytest.mark.integration


class Greeting(BaseModel):
    saludo: str
    idioma: str


async def test_llm_client_classify_smoke(
    db_session,
    tenant_factory,
    llm_calls_schema_ready,
):
    if not get_settings().anthropic_api_key.get_secret_value():
        pytest.skip("Inyectar ANTHROPIC_API_KEY con Infisical antes del test.")
    reset_llm_client_for_tests()
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

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
    assert rows[0].cost_eur > 0
```

## Criterios de aceptación

- `infisical run -- uv run alembic upgrade head` aplica la migración (BD con Postgres accesible).
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
