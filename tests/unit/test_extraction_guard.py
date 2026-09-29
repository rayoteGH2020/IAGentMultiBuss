"""Tope de 2 reintentos automáticos por documento (extraction_guard + settings)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.config import Settings
from app.jobs import extraction_guard
from pydantic import ValidationError

_ENQUEUED = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)


class _FakeRedis:
    """Subconjunto de redis.asyncio usado por el guard (set con TTL y exists)."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex

    async def exists(self, key: str) -> int:
        return int(key in self.values)


def _ctx(job_try: int, enqueue_time: datetime = _ENQUEUED) -> dict[str, Any]:
    return {"job_id": "invoice:abc", "job_try": job_try, "enqueue_time": enqueue_time}


async def _close(ctx: dict[str, Any], redis_conn: _FakeRedis, db: AsyncMock) -> bool:
    return await extraction_guard.close_if_interrupted_after_llm(
        ctx,
        redis_conn,  # type: ignore[arg-type]
        db,
        tenant_id=uuid4(),
        document_kind="invoice",
        document_id=uuid4(),
    )


@pytest.fixture
def abandon(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        extraction_guard.document_processing_service, "abandon_stale_processing", mock
    )
    return mock


async def test_first_run_always_continues(abandon: AsyncMock) -> None:
    redis_conn = _FakeRedis()
    await extraction_guard.mark_llm_started(_ctx(1), redis_conn)  # type: ignore[arg-type]
    db = AsyncMock()

    assert await _close(_ctx(1), redis_conn, db) is False
    abandon.assert_not_awaited()


async def test_rerun_after_slot_retry_continues(abandon: AsyncMock) -> None:
    """Una re-ejecución por Retry del semáforo no llegó al LLM: puede extraer."""
    redis_conn = _FakeRedis()
    db = AsyncMock()

    assert await _close(_ctx(2), redis_conn, db) is False
    abandon.assert_not_awaited()


async def test_rerun_after_llm_started_is_closed_as_interrupted(abandon: AsyncMock) -> None:
    redis_conn = _FakeRedis()
    await extraction_guard.mark_llm_started(_ctx(1), redis_conn)  # type: ignore[arg-type]
    db = AsyncMock()

    assert await _close(_ctx(2), redis_conn, db) is True
    abandon.assert_awaited_once()
    assert abandon.await_args.kwargs["force"] is True
    assert abandon.await_args.kwargs["document_kind"] == "invoice"
    db.commit.assert_awaited_once()


async def test_manual_retry_does_not_inherit_mark(abandon: AsyncMock) -> None:
    """Un reintento manual re-encola el mismo job_id con otra hora: marca nueva."""
    redis_conn = _FakeRedis()
    await extraction_guard.mark_llm_started(_ctx(1), redis_conn)  # type: ignore[arg-type]
    db = AsyncMock()
    later = _ENQUEUED + timedelta(minutes=5)

    assert await _close(_ctx(2, enqueue_time=later), redis_conn, db) is False
    abandon.assert_not_awaited()


async def test_mark_has_ttl_and_is_noop_outside_arq(abandon: AsyncMock) -> None:
    redis_conn = _FakeRedis()
    await extraction_guard.mark_llm_started({}, redis_conn)  # type: ignore[arg-type]
    assert redis_conn.values == {}
    assert await _close({"job_try": 3}, redis_conn, AsyncMock()) is False

    await extraction_guard.mark_llm_started(_ctx(1), redis_conn)  # type: ignore[arg-type]
    (ttl,) = redis_conn.ttls.values()
    assert ttl > 0


def _settings(**overrides: object) -> Settings:
    return Settings(
        app_secret_key="test-secret",  # pragma: allowlist secret
        database_url="postgresql+asyncpg://x@localhost/db",  # pragma: allowlist secret
        redis_url="redis://localhost:6379/0",
        **overrides,  # type: ignore[arg-type]
    )


def test_extraction_max_retries_defaults_to_two() -> None:
    assert _settings().llm_extraction_max_retries == 2


@pytest.mark.parametrize("value", [-1, 3])
def test_extraction_max_retries_is_capped(value: int) -> None:
    with pytest.raises(ValidationError):
        _settings(llm_extraction_max_retries=value)


class _StopCall(Exception):
    """Corta la extracción en cuanto se invoca al cliente LLM."""


@pytest.mark.parametrize(
    "extract_name",
    ["extract_invoice", "extract_ticket", "extract_contract", "extract_insurance"],
)
async def test_document_extraction_uses_configured_max_retries(
    extract_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from app.llm import extraction

    received: dict[str, Any] = {}

    class _FakeClient:
        async def complete(self, **kwargs: Any) -> Any:
            received.update(kwargs)
            raise _StopCall

    async def _prepared(
        file_bytes: bytes, mime_type: str, *, max_pdf_pages: int | None
    ) -> tuple[bytes, str]:
        _ = max_pdf_pages
        return file_bytes, mime_type

    monkeypatch.setattr(extraction, "_prepare_media", _prepared)
    monkeypatch.setattr(extraction, "_build_extraction_messages", lambda **_kw: [])
    monkeypatch.setattr(extraction, "get_llm_client", _FakeClient)
    monkeypatch.setattr(
        extraction, "get_settings", lambda: SimpleNamespace(llm_extraction_max_retries=1)
    )

    with pytest.raises(_StopCall):
        await getattr(extraction, extract_name)(
            file_bytes=b"%PDF-1.4",
            mime_type="application/pdf",
            tenant_id=uuid4(),
            db=AsyncMock(),
        )
    assert received["max_retries"] == 1
