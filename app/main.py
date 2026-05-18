from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.cache import close_redis
from app.core.db import dispose_engine
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import AuthMiddleware
from app.routes.api import health, metrics, webhooks
from app.routes.web import auth as auth_routes
from app.routes.web import chat, demo, home, invoices, jobs, settings


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

    app.add_middleware(AuthMiddleware)

    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    # API routes
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(webhooks.router)

    # Web (HTML)
    app.include_router(auth_routes.router)
    app.include_router(home.router)
    app.include_router(invoices.router)
    app.include_router(jobs.router)
    app.include_router(chat.router)
    app.include_router(settings.router)
    app.include_router(demo.router)

    return app


app = create_app()
