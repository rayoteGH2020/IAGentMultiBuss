"""Cupo de facturas y tickets: jobs, mensajes, avisos y estado en el panel (bloques 2 y 3)."""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.core.document_processing_errors import DocumentErrorCode, is_free_retry
from app.jobs import document_quota_jobs
from app.schemas.document_panel import PanelDocumentRow
from app.services import document_quota_service
from app.services.document_processing_service import retries_exhausted

# ── Mensajes ─────────────────────────────────────────────────────────────────


def test_renewal_is_first_day_of_next_month_in_spanish() -> None:
    assert document_quota_service.renewal_date(date(2026, 12, 1)) == date(2027, 1, 1)
    assert document_quota_service.renewal_label(date(2026, 11, 1)) == "1 de noviembre"


def test_pending_messages_explain_reason_and_when() -> None:
    quota = document_quota_service.pending_message(DocumentErrorCode.monthly_quota)
    budget = document_quota_service.pending_message(DocumentErrorCode.llm_budget)

    assert "facturas y tickets" in quota and "amplíe el cupo" in quota
    assert "presupuesto de IA" in budget
    assert "automáticamente el 1 de" in quota and "automáticamente el 1 de" in budget


def test_duplicate_message_points_to_retry_only_for_failed_documents() -> None:
    match = document_quota_service.DuplicateMatch(
        kind="ticket",
        document_id=uuid4(),
        status="ready",
        created_at=datetime(2026, 10, 3, tzinfo=UTC),
    )
    text = document_quota_service.duplicate_message(match, filename="foto.jpg")
    assert '"foto.jpg" ya está subido como ticket (subido el 03/10/2026)' in text
    assert "Reintentar" not in text

    failed = document_quota_service.DuplicateMatch(
        kind="invoice", document_id=uuid4(), status="failed", created_at=match.created_at
    )
    assert "Reintentar" in document_quota_service.duplicate_message(failed, filename="f.pdf")


# ── Reintentos y panel ───────────────────────────────────────────────────────


def test_free_retries_are_failures_not_caused_by_user() -> None:
    assert is_free_retry(DocumentErrorCode.provider_overload.value)
    assert is_free_retry(DocumentErrorCode.processing_interrupted.value)
    assert not is_free_retry(DocumentErrorCode.extraction_failed.value)
    assert not retries_exhausted(2, DocumentErrorCode.extraction_failed.value)
    assert retries_exhausted(3, DocumentErrorCode.extraction_failed.value)
    assert not retries_exhausted(3, DocumentErrorCode.provider_billing.value)


def _row(**overrides: Any) -> PanelDocumentRow:
    values: dict[str, Any] = {
        "kind": "invoice",
        "id": uuid4(),
        "fecha": None,
        "proveedor": None,
        "cif_nif": None,
        "base_imponible": None,
        "iva_percent": None,
        "iva_amount": None,
        "total": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "status": "failed",
        "source_filename": "f.pdf",
        "error_message": "x",
        "doc_type_code": "factura",
        "doc_type_label": "Factura",
        "error_code": DocumentErrorCode.extraction_failed.value,
    }
    values.update(overrides)
    return PanelDocumentRow(**values)


def test_panel_row_shows_manual_review_after_three_retries() -> None:
    assert _row(manual_retry_count=2).can_retry
    exhausted = _row(manual_retry_count=3)
    assert exhausted.needs_manual_review and not exhausted.can_retry
    # Rechazo por límites: no es «Revisión manual», sigue la vía del SADM.
    limits = _row(manual_retry_count=3, error_code=DocumentErrorCode.too_many_pages.value)
    assert not limits.needs_manual_review and not limits.can_retry


def test_panel_row_quota_pending_has_no_retry() -> None:
    row = _row(status="quota_pending", error_code=DocumentErrorCode.monthly_quota.value)
    assert row.is_quota_pending
    assert not row.can_retry


def _render_row(row: PanelDocumentRow) -> str:
    from app.core.templating import templates

    template = templates.env.get_template("components/document_row.html")
    return " ".join(template.render(document=row, just_uploaded_ids=[]).split())


def test_quota_pending_row_renders_message_without_polling_or_retry() -> None:
    message = document_quota_service.pending_message(DocumentErrorCode.monthly_quota)
    html = _render_row(
        _row(
            status="quota_pending",
            error_code=DocumentErrorCode.monthly_quota.value,
            error_message=message,
        )
    )

    assert "Pendiente de cupo" in html
    assert message in html
    assert "hx-trigger" not in html
    assert "/retry" not in html


def test_failed_row_with_exhausted_retries_shows_manual_review() -> None:
    html = _render_row(_row(manual_retry_count=3))

    assert "Revisión manual" in html
    assert "/retry" not in html
    assert "/retry" in _render_row(_row(manual_retry_count=1))


# ── Jobs ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pending_job_enqueues_only_after_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    db = AsyncMock()
    db.commit = AsyncMock(side_effect=lambda: events.append("commit"))

    @asynccontextmanager
    async def _session(*_args: Any, **_kwargs: Any) -> Any:
        yield db

    tenant_id = uuid4()
    released = [
        document_quota_service.PendingToEnqueue(
            kind="invoice", document_id=uuid4(), tenant_id=tenant_id
        ),
        document_quota_service.PendingToEnqueue(
            kind="ticket", document_id=uuid4(), tenant_id=tenant_id
        ),
    ]
    monkeypatch.setattr(document_quota_jobs, "session_factory_for_worker", _session)
    monkeypatch.setattr(
        document_quota_jobs.document_quota_service,
        "process_pending",
        AsyncMock(return_value=released),
    )
    monkeypatch.setattr(
        document_quota_jobs,
        "enqueue_invoice_processing",
        AsyncMock(side_effect=lambda *_a: events.append("enqueue_invoice")),
    )
    monkeypatch.setattr(
        document_quota_jobs,
        "enqueue_ticket_processing",
        AsyncMock(side_effect=lambda *_a: events.append("enqueue_ticket")),
    )

    result = await document_quota_jobs.process_quota_pending({}, str(tenant_id))

    assert result == {"status": "ok", "released_count": 2}
    assert events == ["commit", "enqueue_invoice", "enqueue_ticket"]


@pytest.mark.asyncio
async def test_pending_cron_continues_after_a_failing_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenants = [uuid4(), uuid4()]
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: tenants))
    )

    @asynccontextmanager
    async def _scope() -> Any:
        yield session

    calls: list[Any] = []

    async def _process(tenant_id: Any) -> int:
        calls.append(tenant_id)
        if tenant_id == tenants[0]:
            raise RuntimeError("bd caída para este tenant")
        return 3

    monkeypatch.setattr(document_quota_jobs, "session_scope", _scope)
    monkeypatch.setattr(document_quota_jobs, "_process_tenant", _process)

    result = await document_quota_jobs.process_quota_pending({})

    assert calls == tenants
    assert result == {"status": "ok", "released_count": 3, "tenant_count": 2}


@pytest.mark.asyncio
async def test_alert_email_has_usage_and_renewal(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import llm_budget_alert_service

    sent: dict[str, str] = {}

    async def _send_email(*, to: str, subject: str, body: str) -> None:
        sent.update(to=to, subject=subject, body=body)

    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one=lambda: SimpleNamespace(name="Peluquería Luna"))
    )
    monkeypatch.setattr("app.core.email.send_email", _send_email)
    monkeypatch.setattr(
        llm_budget_alert_service,
        "tenant_admin",
        AsyncMock(return_value=SimpleNamespace(email="admin@luna.test")),
    )
    monkeypatch.setattr(document_quota_service.entitlement_service, "resolve_tenant", AsyncMock())
    monkeypatch.setattr(
        document_quota_service.monthly_quota_service,
        "bag_usage",
        AsyncMock(return_value=(56, 70)),
    )

    ok = await document_quota_service.send_alert(db, uuid4(), "documents_warning")

    assert ok is True
    assert sent["to"] == "admin@luna.test"
    assert "80 %" in sent["subject"]
    assert "56 de las 70" in sent["body"] and "Peluquería Luna" in sent["body"]


@pytest.mark.asyncio
async def test_alert_without_admin_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import llm_budget_alert_service

    monkeypatch.setattr(llm_budget_alert_service, "tenant_admin", AsyncMock(return_value=None))

    assert (
        await document_quota_service.send_alert(AsyncMock(), uuid4(), "documents_exhausted")
        is False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["invoice", "ticket"])
async def test_worker_holds_document_when_ai_budget_is_exhausted(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    jobs = importlib.import_module(f"app.jobs.{kind}_jobs")
    service = getattr(jobs, f"{kind}_service")
    db = AsyncMock()

    @asynccontextmanager
    async def _ctx(*_args: Any, **_kwargs: Any) -> Any:
        yield db

    row = MagicMock(source_file_key="k", source_mime="application/pdf", source_filename="f.pdf")
    extract = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(jobs, "tenant_invoice_extraction_slot", _ctx)
    monkeypatch.setattr(jobs, "session_factory_for_worker", _ctx)
    monkeypatch.setattr(service, f"get_{kind}", AsyncMock(return_value=row))
    monkeypatch.setattr(jobs.document_processing_service, "begin_processing_attempt", AsyncMock())
    monkeypatch.setattr(jobs.document_processing_service, "finalize_processing_attempt", finalize)
    monkeypatch.setattr(jobs.entitlement_service, "ensure_feature", AsyncMock(return_value=True))
    monkeypatch.setattr(
        jobs.extraction_guard, "close_if_interrupted_after_llm", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        jobs.document_quota_service, "hold_if_budget_exhausted", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(jobs, f"extract_{kind}", extract)

    result = await getattr(jobs, f"process_{kind}")(
        {"redis": MagicMock()}, str(uuid4()), str(uuid4())
    )

    assert result["status"] == "quota_pending"
    extract.assert_not_awaited()
    assert finalize.await_args is not None
    assert finalize.await_args.kwargs["error_code"] == DocumentErrorCode.llm_budget.value
    db.commit.assert_awaited()
