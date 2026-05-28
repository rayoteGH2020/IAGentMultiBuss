"""Tests de encolado ARQ para knowledge (reindex)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.jobs import queue as job_queue


@pytest.mark.asyncio
async def test_purge_arq_job_deletes_job_and_result_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[str] = []
    zrem_calls: list[tuple[str, str]] = []

    fake_pool = AsyncMock()
    fake_pool.delete = AsyncMock(side_effect=lambda *keys: deleted.extend(keys))
    fake_pool.zrem = AsyncMock(side_effect=lambda q, jid: zrem_calls.append((q, jid)))

    monkeypatch.setattr(job_queue, "get_arq_pool", AsyncMock(return_value=fake_pool))

    await job_queue._purge_arq_job("knowledge:abc")

    assert "arq:job:knowledge:abc" in deleted
    assert "arq:result:knowledge:abc" in deleted
    assert zrem_calls == [("arq:queue", "knowledge:abc")]


@pytest.mark.asyncio
async def test_enqueue_knowledge_reindex_purges_before_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purge = AsyncMock()
    monkeypatch.setattr(job_queue, "_purge_arq_job", purge)

    fake_job = MagicMock()
    fake_job.job_id = "knowledge:xyz"
    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock(return_value=fake_job)
    monkeypatch.setattr(job_queue, "get_arq_pool", AsyncMock(return_value=fake_pool))

    doc_id = uuid4()
    tenant_id = uuid4()
    await job_queue.enqueue_knowledge_indexing(doc_id, tenant_id, replace_existing=True)

    purge.assert_awaited_once_with(f"knowledge:{doc_id}")
    fake_pool.enqueue_job.assert_awaited_once()
