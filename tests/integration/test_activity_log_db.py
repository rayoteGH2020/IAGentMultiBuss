"""activity_log en Postgres: permisos de saas_app, inserción en bloque y purga (D029)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from app.models import ActivityLog
from app.services import activity_log_service
from sqlalchemy import Table, delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_TABLE = cast("Table", ActivityLog.__table__)


@pytest.fixture
async def owner_engine(db_session: AsyncSession) -> AsyncIterator[AsyncEngine]:
    """Rol propietario (DATABASE_URL de tests): lee y limpia lo que saas_app no puede."""
    result = await db_session.execute(
        text("SELECT 1 FROM information_schema.tables WHERE table_name = 'activity_log'")
    )
    if result.scalar_one_or_none() is None:
        pytest.skip("Run p79 migration (`infisical run -- bash scripts/test_db_setup.sh`).")
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    yield engine
    await engine.dispose()


def _rows(marker: str, *, age_days: int = 0, count: int = 1) -> list[dict[str, Any]]:
    occurred = datetime.now(UTC) - timedelta(days=age_days)
    return [
        {
            "occurred_at": occurred,
            "kind": "event",
            "source": "api",
            "name": marker,
            "tenant_id": uuid4(),
            "data": {"n": n},
        }
        for n in range(count)
    ]


async def _count(engine: AsyncEngine, marker: str, *, older_than_days: int | None = None) -> int:
    query = select(func.count()).select_from(_TABLE).where(ActivityLog.name == marker)
    if older_than_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        query = query.where(ActivityLog.occurred_at < cutoff)
    async with engine.connect() as conn:
        return int((await conn.execute(query)).scalar_one())


async def _cleanup(engine: AsyncEngine, marker: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(delete(_TABLE).where(ActivityLog.name == marker))


async def test_saas_app_can_only_insert(
    db_session: AsyncSession, owner_engine: AsyncEngine
) -> None:
    role, can_insert, can_select, can_update, can_delete = (
        await db_session.execute(
            text(
                "SELECT current_user, "
                "has_table_privilege(current_user, 'activity_log', 'INSERT'), "
                "has_table_privilege(current_user, 'activity_log', 'SELECT'), "
                "has_table_privilege(current_user, 'activity_log', 'UPDATE'), "
                "has_table_privilege(current_user, 'activity_log', 'DELETE')"
            )
        )
    ).one()
    if role != "saas_app":
        pytest.skip(f"db_session no usa saas_app sino {role}")

    assert (can_insert, can_select, can_update, can_delete) == (True, False, False, False)


async def test_saas_app_bulk_inserts_rows_of_several_tenants(
    db_session: AsyncSession, owner_engine: AsyncEngine
) -> None:
    marker = f"test.bulk.{uuid4().hex[:8]}"
    try:
        # Sin contexto de tenant y con tenants distintos en el mismo INSERT.
        await db_session.execute(_TABLE.insert(), _rows(marker, count=3))
        await db_session.commit()

        assert await _count(owner_engine, marker) == 3
        with pytest.raises(DBAPIError, match="permission denied"):
            await db_session.execute(select(ActivityLog.id).limit(1))
    finally:
        await _cleanup(owner_engine, marker)


async def test_purge_function_enforces_minimum_retention(db_session: AsyncSession) -> None:
    with pytest.raises(DBAPIError, match="retention_days must be >= 7"):
        await db_session.execute(text("SELECT purge_activity_log(3, 100)"))


async def test_purge_deletes_only_rows_older_than_retention(
    db_session: AsyncSession, owner_engine: AsyncEngine
) -> None:
    marker = f"test.purge.{uuid4().hex[:8]}"
    try:
        await db_session.execute(_TABLE.insert(), _rows(marker, age_days=40, count=3))
        await db_session.execute(_TABLE.insert(), _rows(marker, age_days=1, count=2))
        await db_session.commit()

        deleted = (
            await db_session.execute(text("SELECT purge_activity_log(30, 100000)"))
        ).scalar_one()
        await db_session.commit()

        assert deleted >= 3
        assert await _count(owner_engine, marker, older_than_days=30) == 0
        assert await _count(owner_engine, marker) == 2
    finally:
        await _cleanup(owner_engine, marker)


async def test_service_inserts_and_purges_in_batches(owner_engine: AsyncEngine) -> None:
    marker = f"test.service.{uuid4().hex[:8]}"
    try:
        await activity_log_service.insert_batch(_rows(marker, age_days=40, count=5))
        assert await _count(owner_engine, marker) == 5

        deleted = await activity_log_service.purge_expired(30, batch_size=2)

        assert deleted >= 5
        assert await _count(owner_engine, marker) == 0
    finally:
        await _cleanup(owner_engine, marker)


async def test_service_inserts_mixed_row_kinds_from_the_real_builders(
    owner_engine: AsyncEngine,
) -> None:
    """Las filas de cada tipo rellenan columnas distintas; el lote debe entrar igual."""
    from app.core.activity.buffer import build_row
    from app.core.activity.capture import error_row

    marker = f"test.mixed.{uuid4().hex[:8]}"
    try:
        raise ValueError("x")
    except ValueError as exc:
        failure = error_row(exc, name=marker)
    rows = [
        build_row(kind="request", name=marker, method="GET", status_code=200, htmx=False),
        build_row(kind="job", name=marker, attempt=1, outcome="ok", duration_ms=5),
        build_row(kind="event", name=marker, level="info", location="app/x.py:1:f"),
        failure,
    ]
    try:
        await activity_log_service.insert_batch(rows)

        assert await _count(owner_engine, marker) == 4
        async with owner_engine.connect() as conn:
            json_nulls = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM activity_log "
                        "WHERE name = :name AND jsonb_typeof(data) = 'null'"
                    ),
                    {"name": marker},
                )
            ).scalar_one()
        assert json_nulls == 0
    finally:
        await _cleanup(owner_engine, marker)
