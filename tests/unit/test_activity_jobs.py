"""Wrapper de jobs ARQ y propagación de la petición de origen (D029)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from app.core.activity.context import (
    ActivityScope,
    activity_scope,
    current_scope,
    job_parent_kwargs,
)
from app.core.activity.jobs import tracked_job
from arq.worker import Retry


async def process_thing(ctx: dict[str, Any], thing_id: str, tenant_id: str) -> dict[str, Any]:
    scope = current_scope()
    assert scope is not None
    return {"status": "rejected", "seen_tenant": scope.tenant_id, "thing_id": thing_id}


async def explode(ctx: dict[str, Any], tenant_id: str) -> None:
    raise ValueError("Juan Pérez")


async def defer(ctx: dict[str, Any]) -> None:
    raise Retry(defer=5)


def _jobs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r["kind"] == "job"]


async def test_tracked_job_records_row_with_tenant_parent_and_outcome(
    activity_rows: list[dict[str, Any]],
) -> None:
    tenant_id, parent = uuid4(), uuid4()
    wrapped = tracked_job(process_thing)

    result = await wrapped(
        {"job_id": "invoice:1", "job_try": 2},
        "t-1",
        str(tenant_id),
        parent_request_id=str(parent),
    )

    assert wrapped.__qualname__ == "process_thing"
    assert result["seen_tenant"] == tenant_id
    (row,) = _jobs(activity_rows)
    assert row["name"] == "process_thing"
    assert row["source"] == "worker"
    assert (row["job_id"], row["attempt"], row["outcome"]) == ("invoice:1", 2, "rejected")
    assert (row["tenant_id"], row["parent_request_id"]) == (tenant_id, parent)


async def test_tracked_job_records_error_without_message(
    activity_rows: list[dict[str, Any]],
) -> None:
    with pytest.raises(ValueError, match="Juan"):
        await tracked_job(explode)({"job_id": "j"}, tenant_id=str(uuid4()))

    kinds = sorted(r["kind"] for r in activity_rows)
    assert kinds == ["error", "job"]
    assert _jobs(activity_rows)[0]["outcome"] == "error"
    assert "Juan" not in str(activity_rows)


async def test_tracked_job_marks_retry(activity_rows: list[dict[str, Any]]) -> None:
    with pytest.raises(Retry):
        await tracked_job(defer)({"job_id": "j"}, parent_request_id="not-a-uuid")

    (row,) = _jobs(activity_rows)
    assert row["outcome"] == "retry"
    assert row["parent_request_id"] is None


def test_job_parent_kwargs_links_to_request_or_its_parent() -> None:
    assert job_parent_kwargs() == {}

    request_id = uuid4()
    with activity_scope(ActivityScope(source="api", request_id=request_id)):
        assert job_parent_kwargs() == {"parent_request_id": str(request_id)}

    parent = uuid4()
    with activity_scope(ActivityScope(source="worker", job_id="j", parent_request_id=parent)):
        assert UUID(job_parent_kwargs()["parent_request_id"]) == parent


def test_worker_registers_every_job_wrapped_with_original_names() -> None:
    from app.jobs import settings as worker_settings

    names = []
    for entry in worker_settings.WorkerSettings.functions:
        coroutine = getattr(entry, "coroutine", entry)
        assert hasattr(coroutine, "__wrapped__"), coroutine
        names.append(getattr(entry, "name", coroutine.__qualname__))
    assert "process_invoice" in names
    assert "index_knowledge_document" in names
    cron_jobs: list[Any] = list(worker_settings.WorkerSettings.cron_jobs)
    for cron_job in cron_jobs:
        assert hasattr(cron_job.coroutine, "__wrapped__")
    cron_names = [c.name for c in cron_jobs]
    assert "cron:purge_activity_log" in cron_names
    assert worker_settings.WorkerSettings.on_shutdown is worker_settings.shutdown
