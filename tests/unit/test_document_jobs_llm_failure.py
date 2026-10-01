"""Workers de documentos ante LLMCompleteError: detalle técnico a BD, log sin contenido."""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.core.document_processing_errors import DocumentErrorCode
from app.core.errors import LLMCompleteError

SECRET = "Juan Perez 12345678Z"  # pragma: allowlist secret


@asynccontextmanager
async def _noop_ctx(*_args: Any, **_kwargs: Any) -> Any:
    yield None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ticket", "contract", "insurance"])
async def test_llm_failure_persists_raw_error_and_logs_safe_message(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    jobs = importlib.import_module(f"app.jobs.{kind}_jobs")
    service = getattr(jobs, f"{kind}_service")
    db = AsyncMock()

    @asynccontextmanager
    async def _session(*_args: Any, **_kwargs: Any) -> Any:
        yield db

    row = MagicMock(source_file_key="k", source_mime="application/pdf", source_filename="f.pdf")
    monkeypatch.setattr(jobs, "tenant_invoice_extraction_slot", _noop_ctx)
    monkeypatch.setattr(jobs, "session_factory_for_worker", _session)
    monkeypatch.setattr(jobs, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(jobs, "get_redis", MagicMock())
    monkeypatch.setattr(jobs, "get_storage", lambda: MagicMock(download_bytes=AsyncMock()))
    monkeypatch.setattr(service, f"get_{kind}", AsyncMock(return_value=row))
    mark_failed = AsyncMock()
    monkeypatch.setattr(service, "mark_failed", mark_failed)
    monkeypatch.setattr(jobs.document_processing_service, "begin_processing_attempt", AsyncMock())
    monkeypatch.setattr(jobs.entitlement_service, "ensure_feature", AsyncMock(return_value=True))
    if hasattr(jobs, "document_quota_service"):  # facturas y tickets: presupuesto de IA disponible
        monkeypatch.setattr(
            jobs.document_quota_service, "hold_if_budget_exhausted", AsyncMock(return_value=False)
        )

    llm_call_id = uuid4()
    error = LLMCompleteError(
        "LLM call failed (ValidationError)",
        llm_call_id=llm_call_id,
        raw_error=f"1 validation error for {kind}: input_value='{SECRET}'",
    )
    monkeypatch.setattr(jobs, f"extract_{kind}", AsyncMock(side_effect=error))
    log = MagicMock()
    monkeypatch.setattr(jobs, "logger", log)

    result = await getattr(jobs, f"process_{kind}")(
        {"redis": MagicMock()}, str(uuid4()), str(uuid4())
    )

    assert result["status"] == "failed"
    kwargs = mark_failed.await_args.kwargs
    # La UI traduce el texto técnico (qué campo falló): se persiste el raw (RLS).
    assert SECRET in kwargs["error"]
    assert kwargs["llm_call_id"] == llm_call_id
    assert kwargs["error_code"] is DocumentErrorCode.extraction_failed
    # El traceback del log formatea str(exc), que es el mensaje seguro.
    assert SECRET not in str(error)
    assert SECRET not in repr(log.mock_calls)
