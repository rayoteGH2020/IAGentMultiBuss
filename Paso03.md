# Paso 03 — FastAPI base, configuración y health checks

## Objetivo

Crear la app FastAPI mínima funcional con:
- Configuración desde **variables de entorno** (`pydantic-settings`, `env_file=None`); en local se inyectan con **Infisical** (`infisical run -- ...`, ver `Agents.md` §2).
- Conexión async a Postgres (SQLAlchemy 2.0 + asyncpg) y a Redis.
- Logging estructurado con `structlog`.
- Endpoints `/health`, `/health/db` y `/health/redis`.
- Manejo de excepciones centralizado.

Al final, `infisical run -- uv run uvicorn app.main:app --reload` arranca la app y los healthchecks responden `200 OK` mientras los servicios del Paso 02 estén levantados y las variables estén en Infisical.

## Pre-requisitos

- Pasos 01 y 02 completados.
- Servicios Docker levantados (`./scripts/dev_up.sh`).
- **Infisical** configurado (CLI + proyecto/entorno `dev` o equivalente) con al menos `DATABASE_URL`, `REDIS_URL`, `APP_SECRET_KEY` y el resto de claves obligatorias según `docs/environment-variables.md`. No usar fichero `.env`.

## Contexto relevante

- `arquitectura.md` secciones 4 (Estructura) y 11 (Observabilidad).
- `Agents.md` secciones 1 (Stack), **§2 (Infisical, sin `.env`)**, 3 (Capas), 4 (Convenciones Python).

## Tareas

- [ ] Implementar `app/config.py` con `Settings` basado en `pydantic-settings`.
- [ ] Implementar `app/core/db.py` con engine y session factory async.
- [ ] Implementar `app/core/cache.py` con cliente Redis async.
- [ ] Implementar `app/core/logging.py` con configuración de `structlog`.
- [ ] Implementar `app/core/errors.py` con excepciones base y handlers.
- [ ] Implementar `app/deps.py` con `get_db()` y `get_redis()`.
- [ ] Implementar `app/routes/api/health.py` con los 3 endpoints.
- [ ] Implementar `app/main.py` que monte todo.
- [ ] Verificar arranque con `infisical run -- uv run uvicorn ...`.
- [ ] Verificar `curl http://localhost:8000/health` → `{"status": "ok"}`.
- [ ] Verificar `curl http://localhost:8000/health/db` → `{"status": "ok"}`.
- [ ] Verificar `curl http://localhost:8000/health/redis` → `{"status": "ok"}`.
- [ ] Commit: `feat: fastapi base with config, db, redis, health`.

## Detalles técnicos

### `app/config.py`

```python
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_env: Literal["development", "staging", "production"] = "development"
    app_secret_key: SecretStr
    app_base_url: str = "http://localhost:8000"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Database
    database_url: str
    redis_url: str

    # Storage (R2) — se usan en Paso 11
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: SecretStr = SecretStr("")
    r2_bucket: str = "saas-files"
    r2_public_url: str = ""
    r2_region: str = "auto"

    # Auth (Clerk) — se usan en Paso 07
    clerk_secret_key: SecretStr = SecretStr("")
    clerk_publishable_key: str = ""
    clerk_jwks_url: str = ""
    clerk_webhook_secret: SecretStr = SecretStr("")

    # LLM providers — se usan en Paso 10
    anthropic_api_key: SecretStr = SecretStr("")
    google_api_key: SecretStr = SecretStr("")
    voyage_api_key: SecretStr = SecretStr("")

    # Observability
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_host: str = "http://localhost:3000"

    # Crypto
    encryption_key: SecretStr = SecretStr("")

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

### `app/core/db.py`

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Context manager para uso fuera de FastAPI (jobs, scripts)."""
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
```

### `app/core/cache.py`

```python
import redis.asyncio as redis

from app.config import get_settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        settings = get_settings()
        _client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            health_check_interval=30,
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
```

### `app/core/logging.py`

```python
import logging
import sys

import structlog
from structlog.types import Processor

from app.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.is_dev:
        renderer: Processor = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Redirigir logs estándar a structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    # Silenciar logs verbosos
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
```

### `app/core/errors.py`

```python
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

log = get_logger(__name__)


class AppError(Exception):
    """Base para excepciones de dominio."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ValidationError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "validation_error"


class AuthError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


class ExternalServiceError(AppError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "external_service_error"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        log.warning(
            "app_error",
            code=exc.code,
            message=exc.message,
            details=exc.details,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={"code": "internal_error", "message": "Internal server error"},
        )
```

### `app/deps.py`

```python
from collections.abc import AsyncIterator

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_redis
from app.core.db import get_sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_redis_dep() -> redis.Redis:
    return get_redis()
```

### `app/routes/api/health.py`

```python
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import redis.asyncio as redis

from app.deps import get_db, get_redis_dep

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/db")
async def health_db(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    result = await db.execute(text("SELECT 1"))
    assert result.scalar_one() == 1
    return {"status": "ok"}


@router.get("/health/redis")
async def health_redis(r: redis.Redis = Depends(get_redis_dep)) -> dict[str, str]:
    pong = await r.ping()
    assert pong is True
    return {"status": "ok"}
```

### `app/main.py`

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.cache import close_redis
from app.core.db import dispose_engine
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.routes.api import health


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log = get_logger(__name__)
    log.info("app.starting")
    yield
    log.info("app.shutting_down")
    await dispose_engine()
    await close_redis()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Mi SaaS",
        version="0.1.0",
        lifespan=lifespan,
    )

    register_error_handlers(app)

    # API routes
    app.include_router(health.router)

    return app


app = create_app()
```

### `app/routes/api/__init__.py`

```python
# Exporta routers para conveniencia
```

## Criterios de aceptación

- [ ] `infisical run -- uv run uvicorn app.main:app --reload` arranca sin errores.
- [ ] Logs salen en consola con formato legible (color en dev).
- [ ] `curl http://localhost:8000/health` → `{"status":"ok"}`.
- [ ] `curl http://localhost:8000/health/db` → `{"status":"ok"}`.
- [ ] `curl http://localhost:8000/health/redis` → `{"status":"ok"}`.
- [ ] Si paras Postgres, `/health/db` devuelve 500 con log estructurado.
- [ ] `curl http://localhost:8000/docs` muestra Swagger con los 3 endpoints.
- [ ] `uv run mypy app` pasa.
- [ ] `uv run ruff check .` pasa.
- [ ] Commit hecho.

## Comandos útiles

```bash
# Arrancar app (secretos vía Infisical)
infisical run -- uv run uvicorn app.main:app --reload

# Con log-level específico (también puede ir en Infisical)
infisical run -- env LOG_LEVEL=DEBUG uv run uvicorn app.main:app --reload

# Probar health checks
curl -s http://localhost:8000/health | jq
curl -s http://localhost:8000/health/db | jq
curl -s http://localhost:8000/health/redis | jq

# Lint y tipos
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```

## Lo que NO toca este paso

- Modelos de BD: Paso 06.
- Autenticación: Paso 07.
- Frontend / templates: Paso 04.
- Routes web (HTML): Paso 04 en adelante.

## Posibles problemas

**`infisical run` falla o no inyecta variables**: `infisical login`, comprobar que el proyecto y el entorno (`dev`, etc.) son los correctos y que existen las claves en la UI de Infisical.

**`Settings` falla al construir** (campos requeridos faltantes): comprueba que ejecutas con `infisical run -- ...`, que el entorno de Infisical es el correcto y que los nombres en MAYÚSCULAS coinciden con los campos de `Settings`. `pydantic-settings` es case-insensitive por defecto respecto al entorno.

**`asyncpg` da error de conexión**: confirma que la URL es `postgresql+asyncpg://` (con `+asyncpg`), no `postgresql://`.

**Redis da `ConnectionError`**: confirma que `REDIS_URL=redis://localhost:6379/0`, sin contraseña en dev.

**Logs salen en JSON en dev**: revisa `app_env`. En dev usa renderer ConsoleRenderer (colores). En staging/production, JSON.

**mypy se queja de `Settings()`**: añade `# type: ignore[call-arg]` en `get_settings()` (los campos vienen del entorno en runtime, mypy no lo sabe).

## Siguiente paso

`Paso04.md` — Frontend base: descargar Tailwind CLI standalone, HTMX y Alpine como estáticos, crear `base.html` y `pages/demo.html` con una página de prueba que demuestra que la stack frontend funciona.
