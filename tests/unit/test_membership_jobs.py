"""Job ARQ de bajas vencidas: registro en el worker y delegación al servicio."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from app.jobs import membership_jobs
from app.jobs.settings import WorkerSettings


def test_expire_member_removals_is_scheduled_every_15_minutes() -> None:
    jobs = [job for job in WorkerSettings.cron_jobs if job.name.endswith("expire_member_removals")]
    assert len(jobs) == 1
    job = jobs[0]
    assert job.minute == {0, 15, 30, 45}
    assert job.run_at_startup is True
    assert job.unique is True


@pytest.mark.asyncio
async def test_expire_member_removals_delegates_to_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = object()

    @asynccontextmanager
    async def fake_scope() -> AsyncIterator[object]:
        yield db

    execute = AsyncMock(return_value=3)
    monkeypatch.setattr(membership_jobs, "session_scope", fake_scope)
    monkeypatch.setattr(membership_jobs.membership_service, "execute_due_removals", execute)

    assert await membership_jobs.expire_member_removals({}) == 3
    execute.assert_awaited_once_with(db)
