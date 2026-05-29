from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.core.cache import close_redis
from app.core.db import dispose_engine
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import AuthMiddleware
from app.routes.api import health, metrics, webhooks, webhooks_telegram, webhooks_whatsapp
from app.routes.web import (
    admin_channel_integrations,
    chat,
    demo,
    documents,
    home,
    integrations,
    jobs,
    knowledge,
    settings,
)
from app.routes.web import auth as auth_routes


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # `lifespan` es el mecanismo de FastAPI para código de arranque/apagado que
    # se ejecuta una sola vez por proceso, no por request. Lo que está antes del
    # `yield` corre al iniciar; lo que está después, al apagar.
    configure_logging()
    log = get_logger(__name__)
    log.info("app.starting")
    yield
    log.info("app.shutting_down")
    # Se cierran explícitamente los pools de conexión para que el proceso
    # termine limpio. Sin esto, asyncpg y aioredis pueden lanzar warnings de
    # "event loop closed" o dejar conexiones abiertas en Postgres/Redis.
    await dispose_engine()
    await close_redis()


def create_app() -> FastAPI:
    # Patrón factory: devuelve una instancia nueva cada vez que se llama.
    # Permite que los tests creen su propia instancia de la app en aislamiento
    # sin compartir estado global con otras ejecuciones.
    app = FastAPI(
        title="Mi SaaS",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Centraliza el manejo de excepciones de dominio (NotFoundError,
    # ForbiddenError, etc.) para que todos los routers devuelvan respuestas HTTP
    # consistentes sin repetir try/except en cada endpoint.
    register_error_handlers(app)

    # El middleware de auth se registra antes que los routers para que se
    # ejecute en cada request, independientemente de la ruta.
    # FastAPI aplica los middlewares en orden LIFO (el último añadido se ejecuta
    # primero), así que AuthMiddleware siempre corre antes de cualquier handler.
    app.add_middleware(AuthMiddleware)

    # El directorio es relativo a la raíz del proyecto, que es desde donde
    # Uvicorn debe lanzarse (`uv run uvicorn app.main:app`).
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    # --- Rutas API (devuelven JSON) ---
    # health: /health, /health/db, /health/redis — usados por Coolify/load balancer.
    app.include_router(health.router)
    # metrics: /metrics/module1 — consumido por dashboards internos (token-gated).
    app.include_router(metrics.router)
    # webhooks: callbacks de Clerk y otros servicios externos.
    app.include_router(webhooks.router)
    app.include_router(webhooks_whatsapp.router)
    app.include_router(webhooks_telegram.router)

    # --- Rutas web (devuelven HTML via Jinja2 + patrón página/fragmento HTMX) ---
    app.include_router(auth_routes.router)
    app.include_router(home.router)
    # documents: lista unificada, subida y panel de documentos (módulo 1).
    app.include_router(documents.router)
    # knowledge: base de conocimiento RAG (módulo 2, Paso 18).
    app.include_router(knowledge.router)
    # jobs: endpoint de polling HTMX `/jobs/{invoice,ticket,knowledge}/{id}/status`.
    app.include_router(jobs.router)
    app.include_router(chat.router)
    app.include_router(settings.router)
    app.include_router(integrations.router)
    app.include_router(integrations.auth_router)
    app.include_router(admin_channel_integrations.router)
    app.include_router(demo.router)

    @app.get("/invoices", include_in_schema=False)
    async def legacy_invoices_root() -> RedirectResponse:
        return RedirectResponse(url="/documents", status_code=308)

    @app.get("/invoices/{path:path}", include_in_schema=False)
    async def legacy_invoices_path(path: str) -> RedirectResponse:
        return RedirectResponse(url=f"/documents/{path}", status_code=308)

    return app


# Se instancia la app en el ámbito de módulo para que Uvicorn pueda
# apuntar a `app.main:app` como entry point. No se llama a create_app()
# en ningún otro lugar del código de producción para no crear instancias extra.
app = create_app()
