"""Integración: tabla audit_log tras migración p16_audit_01."""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_audit_log_table_exists(audit_schema_ready: None, db_session) -> None:
    result = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'audit_log'"
        ),
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_audit_log_rls_enabled(audit_schema_ready: None, db_session) -> None:
    result = await db_session.execute(
        text("SELECT relrowsecurity FROM pg_class " "WHERE relname = 'audit_log'"),
    )
    assert result.scalar_one() is True
