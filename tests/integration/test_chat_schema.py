"""Integración: tablas de chat existen tras migración p16."""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_chat_tables_exist(chat_schema_ready: None, db_session) -> None:
    for table in ("chat_threads", "chat_messages"):
        result = await db_session.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :name"
            ),
            {"name": table},
        )
        assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_pg_trgm_and_unaccent_extensions(chat_schema_ready: None, db_session) -> None:
    result = await db_session.execute(
        text(
            "SELECT extname FROM pg_extension "
            "WHERE extname IN ('pg_trgm', 'unaccent') ORDER BY extname"
        ),
    )
    names = [row[0] for row in result.all()]
    assert names == ["pg_trgm", "unaccent"]
