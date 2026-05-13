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
