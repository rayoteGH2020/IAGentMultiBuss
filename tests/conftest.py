"""Fixtures compartidas (Postgres con rol RLS para tests de integración)."""

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import pytest
from app.models import Tenant
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def pytest_configure(config: pytest.Config) -> None:
    """Valores mínimos para importar `app` en tests sin Infisical."""
    os.environ.setdefault("APP_SECRET_KEY", "test-app-secret-not-for-production")
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+asyncpg://saas_app:saas@localhost:5432/saas",  # pragma: allowlist secret
    )
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


_DEFAULT_RLS_URL = (
    "postgresql+asyncpg://saas_app:saas@localhost:5432/saas"  # pragma: allowlist secret
)


@pytest.fixture
def invoices_migration_applied_sync(rls_database_url: str) -> None:
    """SKIP si Postgres no tiene la tabla `invoices` (migración p09 pendiente)."""

    async def probe() -> None:
        engine = create_async_engine(rls_database_url, pool_pre_ping=True)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name = 'invoices'"
                    ),
                )
                if result.scalar_one_or_none() is None:
                    pytest.skip("Run Paso09 migration (`uv run alembic upgrade head`).")
        except SQLAlchemyError:
            pytest.skip("Postgres no disponible para tests de integración.")
        finally:
            await engine.dispose()

    asyncio.run(probe())


@pytest.fixture
def rls_database_url() -> str:
    return os.environ.get("RLS_TEST_DATABASE_URL", _DEFAULT_RLS_URL)


@pytest.fixture
async def db_session(rls_database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(rls_database_url, pool_pre_ping=True)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with sm() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest.fixture
async def tenant_factory(
    db_session: AsyncSession,
) -> Callable[..., Coroutine[Any, Any, Tenant]]:
    from uuid import uuid4

    async def _make(name: str | None = None) -> Tenant:
        t = Tenant(name=name or f"T {uuid4().hex[:8]}")
        db_session.add(t)
        await db_session.flush()
        return t

    return _make


@pytest.fixture
async def invoices_schema_ready(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'invoices'"
        ),
    )
    if result.scalar_one_or_none() is None:
        pytest.skip("Run Paso09 migration (`uv run alembic upgrade head`).")
