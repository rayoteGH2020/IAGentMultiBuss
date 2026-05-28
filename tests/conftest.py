"""Fixtures compartidas (Postgres con rol RLS para tests de integración)."""

import os
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import pytest
from app.models import Tenant
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


def pytest_configure(config: pytest.Config) -> None:
    """Valores mínimos para importar `app` en tests sin Infisical."""
    os.environ.setdefault("APP_SECRET_KEY", "test-app-secret-not-for-production")
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+asyncpg://saas_app:saas@localhost:5432/saas",  # pragma: allowlist secret
    )
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    # Python 3.14 + Windows + asyncpg: QueuePool.dispose() deja el ProactorEventLoop
    # en estado inválido. NullPool evita la contaminación entre tests function-scoped.
    from app.core.db import use_null_pool_for_tests

    use_null_pool_for_tests()


_DEFAULT_RLS_URL = (
    "postgresql+asyncpg://saas_app:saas@localhost:5432/saas"  # pragma: allowlist secret
)


@pytest.fixture
async def invoices_migration_applied(rls_database_url: str) -> None:
    """SKIP si Postgres no tiene la tabla `invoices` (migración p09 pendiente)."""
    # NullPool: evita mantener conexiones asyncpg abiertas en el pool entre el
    # setup de este fixture y el cuerpo del test. En Python 3.14 + Windows,
    # asyncpg.pool.dispose() puede dejar el ProactorEventLoop en estado inválido
    # para conexiones posteriores en el mismo loop.
    engine = create_async_engine(rls_database_url, poolclass=NullPool)
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


@pytest.fixture
def rls_database_url() -> str:
    return os.environ.get("RLS_TEST_DATABASE_URL", _DEFAULT_RLS_URL)


@pytest.fixture
async def db_session(rls_database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(rls_database_url, poolclass=NullPool)
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
    doc_types = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'doc_types'"
        ),
    )
    if doc_types.scalar_one_or_none() is None:
        pytest.skip("Run Paso11 migration (`uv run alembic upgrade head`).")
    tickets = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'tickets'"
        ),
    )
    if tickets.scalar_one_or_none() is None:
        pytest.skip("Run Paso11 migration (`uv run alembic upgrade head`).")


@pytest.fixture
async def chat_schema_ready(db_session: AsyncSession) -> None:
    for table in ("chat_threads", "chat_messages"):
        result = await db_session.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                f"WHERE table_schema = 'public' AND table_name = '{table}'"
            ),
        )
        if result.scalar_one_or_none() is None:
            pytest.skip("Run Paso16 migration (`uv run alembic upgrade head`).")
    citations_col = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'chat_messages' "
            "AND column_name = 'citations'"
        ),
    )
    if citations_col.scalar_one_or_none() is None:
        pytest.skip("Run Paso20 migration (`uv run alembic upgrade head`).")


@pytest.fixture
async def audit_schema_ready(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'audit_log'"
        ),
    )
    if result.scalar_one_or_none() is None:
        pytest.skip("Run Paso16 audit migration (`uv run alembic upgrade head`).")


@pytest.fixture
async def llm_calls_schema_ready(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'llm_calls'"
        ),
    )
    if result.scalar_one_or_none() is None:
        pytest.skip("Run Paso10 migration (`uv run alembic upgrade head`).")


@pytest.fixture
async def usage_meter_schema_ready(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'usage_meter'"
        ),
    )
    if result.scalar_one_or_none() is None:
        pytest.skip("Run Paso20 usage_meter migration (`uv run alembic upgrade head`).")


@pytest.fixture
async def knowledge_schema_ready(db_session: AsyncSession) -> None:
    for table in ("knowledge_documents", "knowledge_chunks"):
        result = await db_session.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                f"WHERE table_schema = 'public' AND table_name = '{table}'"
            ),
        )
        if result.scalar_one_or_none() is None:
            pytest.skip("Run Paso18 migration (`uv run alembic upgrade head`).")
