"""Buffer y volcado de activity_log (D029)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from app.core.activity import buffer as buffer_module
from app.core.activity.buffer import ActivityBuffer, ActivityFlusher, build_row, record
from app.core.activity.context import set_process_source


def test_buffer_drops_oldest_when_full() -> None:
    buffer = ActivityBuffer(maxlen=2)
    for n in range(3):
        buffer.add({"n": n})

    assert buffer.dropped == 1
    assert buffer.drain(10) == [{"n": 1}, {"n": 2}]


def test_record_is_a_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        buffer_module, "get_settings", lambda: SimpleNamespace(activity_log_enabled=False)
    )
    before = len(buffer_module.get_buffer())

    record(build_row(kind="event", name="x"))

    assert len(buffer_module.get_buffer()) == before


def test_build_row_outside_scope_uses_process_source() -> None:
    set_process_source("worker")
    try:
        row = build_row(kind="event", name="n" * 200, level="info")
    finally:
        set_process_source("api")

    assert row["source"] == "worker"
    assert len(row["name"]) == 120
    assert row["request_id"] is None
    assert row["level"] == "info"


async def test_flush_writes_in_batches_and_reports_drops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(buffer_module, "WRITE_BATCH_ROWS", 2)
    buffer = ActivityBuffer(maxlen=10)
    for n in range(5):
        buffer.add({"n": n})
    buffer.dropped = 4
    batches: list[list[dict[str, Any]]] = []

    async def writer(rows: list[dict[str, Any]]) -> None:
        batches.append(rows)

    written = await ActivityFlusher(writer, buffer=buffer).flush()

    assert written == 5
    assert [len(b) for b in batches] == [2, 2, 1]
    assert buffer.dropped == 0


async def test_flush_failure_loses_batch_without_raising() -> None:
    buffer = ActivityBuffer()
    buffer.add({"n": 1})

    async def writer(_rows: list[dict[str, Any]]) -> None:
        raise ConnectionError("db down")

    assert await ActivityFlusher(writer, buffer=buffer).flush() == 0
    assert len(buffer) == 0


async def test_flusher_writes_periodically_and_on_stop() -> None:
    buffer = ActivityBuffer()
    written: list[dict[str, Any]] = []

    async def writer(rows: list[dict[str, Any]]) -> None:
        written.extend(rows)

    flusher = ActivityFlusher(writer, buffer=buffer, interval=0.1)
    flusher.start()
    buffer.add({"n": 1})
    await asyncio.sleep(0.5)
    assert written == [{"n": 1}]

    buffer.add({"n": 2})
    await flusher.stop()
    assert written == [{"n": 1}, {"n": 2}]
