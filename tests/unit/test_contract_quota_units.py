"""Contratos (bloque 5): tramos de páginas, config, panel, jobs y tools del chat."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.config import Settings
from app.core.document_processing_errors import DocumentErrorCode
from app.core.entitlement_codes import LIMIT_CONTRACT_MAX_PAGES
from app.jobs import contract_jobs, document_quota_jobs
from app.llm.tools.document_chat import (
    AggregateDocumentsArgs,
    SearchDocumentsArgs,
    _filters_from_aggregate_args,
    _filters_from_search_args,
)
from app.schemas.document_panel import PanelDocumentRow
from app.schemas.entitlements import Entitlements
from app.services import contract_quota_service, document_quota_service
from app.services.document_processing_service import (
    CONTRACT_PROCESSING_STALE_AFTER_SECONDS,
    processing_stale_after,
)
from pydantic import ValidationError

_BASE = {
    "app_secret_key": "x" * 32,
    "database_url": "postgresql+asyncpg://u:p@localhost/db",  # pragma: allowlist secret
    "redis_url": "redis://localhost:6379/0",
    "clerk_jwks_url": "",
    "webhook_allow_unsigned": False,
    "langfuse_capture_content": False,
}


# ── Tramos de páginas ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("pages", "units"),
    [(1, 1), (30, 1), (31, 2), (60, 2), (61, 3), (100, 3)],
)
def test_upload_units_follow_default_page_tiers(pages: int, units: int) -> None:
    assert contract_quota_service.upload_units(pages, (30, 60)) == units


def test_upload_units_use_configured_tiers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        contract_quota_service,
        "get_settings",
        lambda: SimpleNamespace(contract_upload_page_tiers=[10]),
    )
    assert contract_quota_service.upload_units(10) == 1
    assert contract_quota_service.upload_units(11) == 2
    assert contract_quota_service.upload_units(500) == 2


def test_page_tiers_env_accepts_csv_and_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTRACT_UPLOAD_PAGE_TIERS", "20, 50")
    assert Settings(**_BASE).contract_upload_page_tiers == [20, 50]
    monkeypatch.setenv("CONTRACT_UPLOAD_PAGE_TIERS", "[40]")
    assert Settings(**_BASE).contract_upload_page_tiers == [40]
    monkeypatch.delenv("CONTRACT_UPLOAD_PAGE_TIERS")
    assert Settings(**_BASE).contract_upload_page_tiers == [30, 60]


@pytest.mark.parametrize("raw", ["60,30", "0,30", "30,30", "-5", "1,2,3,4,5,6,7,8,9,10"])
def test_page_tiers_reject_unordered_or_invalid_values(raw: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{**_BASE, "contract_upload_page_tiers": raw})


def _ents(max_pages: Decimal | None) -> Entitlements:
    return Entitlements(
        plan_code="basic", features=frozenset(), limits={LIMIT_CONTRACT_MAX_PAGES: max_pages}
    )


def test_max_pages_is_plan_limit_capped_by_worker_ceiling() -> None:
    assert contract_quota_service.max_pages(_ents(Decimal("40"))) == 40
    # Ilimitado o mayor que el techo del worker: manda DOCUMENT_OVERRIDE_MAX_PDF_PAGES.
    assert contract_quota_service.max_pages(_ents(None)) == 100
    assert contract_quota_service.max_pages(_ents(Decimal("500"))) == 100


def test_too_many_pages_message_names_plan_cap() -> None:
    message = contract_quota_service.too_many_pages_message(
        filename="c.pdf", detail="120 páginas; el máximo admitido son 100", cap=100
    )
    assert '"c.pdf"' in message
    assert "hasta 100 páginas" in message


def test_contract_pending_message_mentions_contract_altas() -> None:
    message = document_quota_service.pending_message(
        DocumentErrorCode.monthly_quota, "contract", renewal=datetime(2026, 12, 1).date()
    )
    assert "altas de contratos" in message
    assert "1 de diciembre" in message


# ── Procesado largo ──────────────────────────────────────────────────────────


def test_contracts_wait_longer_before_being_considered_interrupted() -> None:
    from app.config import get_settings

    base = get_settings().document_processing_stale_after_seconds
    assert processing_stale_after("contract") == max(base, CONTRACT_PROCESSING_STALE_AFTER_SECONDS)
    assert processing_stale_after("invoice") == base


def _row(**overrides: Any) -> PanelDocumentRow:
    values: dict[str, Any] = {
        "kind": "contract",
        "id": uuid4(),
        "fecha": None,
        "proveedor": "Acme",
        "cif_nif": None,
        "base_imponible": None,
        "iva_percent": None,
        "iva_amount": None,
        "total": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "status": "ready",
        "source_filename": "c.pdf",
        "error_message": None,
        "doc_type_code": "contrato",
        "doc_type_label": "Contrato",
    }
    values.update(overrides)
    return PanelDocumentRow(**values)


def test_processing_contract_offers_retry_only_after_contract_threshold() -> None:
    five_minutes_ago = datetime.now(UTC) - timedelta(minutes=5)
    assert not _row(status="processing", updated_at=five_minutes_ago).can_retry
    eleven_minutes_ago = datetime.now(UTC) - timedelta(minutes=11)
    assert _row(status="processing", updated_at=eleven_minutes_ago).can_retry
    # Una factura con 5 minutos sí se da por interrumpida.
    assert _row(kind="invoice", status="processing", updated_at=five_minutes_ago).can_retry


# ── Panel: sustituido ────────────────────────────────────────────────────────


def _contract(lifecycle: str) -> Any:
    contract = MagicMock()
    contract.lifecycle = SimpleNamespace(value=lifecycle)
    contract.currency = "EUR"
    contract.titulo = "Mantenimiento"
    for attr in (
        "numero_contrato",
        "fecha_fin",
        "fecha_firma",
        "importe_periodico",
        "importe_total",
        "importe_anual",
        "objeto",
        "iva_incluido",
        "periodicidad",
    ):
        setattr(contract, attr, None)
    contract.source_filename = "c.pdf"
    contract.llm_call = None
    return contract


def _render_row(row: PanelDocumentRow) -> str:
    from app.core.templating import templates

    template = templates.env.get_template("components/document_row.html")
    return " ".join(template.render(document=row, just_uploaded_ids=[]).split())


def test_active_contract_offers_mark_as_replaced() -> None:
    row = _row(contract=_contract("active"))
    assert row.can_change_lifecycle and not row.is_replaced
    html = _render_row(row)
    assert f"/documents/contract/{row.id}/replace" in html
    assert "Marcar como sustituido" in html
    assert "hx-confirm" in html
    assert ">Sustituido<" not in html


def test_replaced_contract_shows_badge_and_reactivate() -> None:
    row = _row(contract=_contract("replaced"))
    assert row.is_replaced
    html = _render_row(row)
    assert ">Sustituido<" in html
    assert f"/documents/contract/{row.id}/reactivate" in html
    assert "Volver a vigente" in html


def test_lifecycle_actions_only_for_processed_contracts() -> None:
    assert not _row(status="failed", contract=_contract("active")).can_change_lifecycle
    assert not _row(kind="invoice", doc_type_code="factura").can_change_lifecycle


# ── Jobs ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pending_job_enqueues_released_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    db = AsyncMock()

    @asynccontextmanager
    async def _session(*_args: Any, **_kwargs: Any) -> Any:
        yield db

    tenant_id = uuid4()
    contract_id = uuid4()
    released = [
        document_quota_service.PendingToEnqueue(
            kind="contract", document_id=contract_id, tenant_id=tenant_id
        )
    ]
    enqueue = AsyncMock()
    monkeypatch.setattr(document_quota_jobs, "session_factory_for_worker", _session)
    monkeypatch.setattr(
        document_quota_jobs.document_quota_service,
        "process_pending",
        AsyncMock(return_value=released),
    )
    monkeypatch.setattr(document_quota_jobs, "enqueue_contract_processing", enqueue)

    result = await document_quota_jobs.process_quota_pending({}, str(tenant_id))

    assert result["released_count"] == 1
    enqueue.assert_awaited_once_with(contract_id, tenant_id)


@pytest.mark.asyncio
async def test_alert_job_accepts_contract_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def _session(*_args: Any, **_kwargs: Any) -> Any:
        yield AsyncMock()

    send = AsyncMock(return_value=True)
    monkeypatch.setattr(document_quota_jobs, "session_factory_for_worker", _session)
    monkeypatch.setattr(document_quota_jobs.document_quota_service, "send_alert", send)

    result = await document_quota_jobs.send_documents_quota_alert(
        {}, str(uuid4()), "contracts_exhausted"
    )
    assert result["status"] == "sent"
    assert send.await_args is not None
    assert send.await_args.args[2] == "contracts_exhausted"
    unknown = await document_quota_jobs.send_documents_quota_alert({}, str(uuid4()), "bogus")
    assert unknown["status"] == "skipped"


def _worker_mocks(monkeypatch: pytest.MonkeyPatch, db: AsyncMock) -> MagicMock:
    @asynccontextmanager
    async def _ctx(*_args: Any, **_kwargs: Any) -> Any:
        yield db

    row = MagicMock(source_file_key="k", source_mime="application/pdf", source_filename="f.pdf")
    monkeypatch.setattr(contract_jobs, "tenant_invoice_extraction_slot", _ctx)
    monkeypatch.setattr(contract_jobs, "session_factory_for_worker", _ctx)
    monkeypatch.setattr(contract_jobs.contract_service, "get_contract", AsyncMock(return_value=row))
    monkeypatch.setattr(
        contract_jobs.document_processing_service, "begin_processing_attempt", AsyncMock()
    )
    monkeypatch.setattr(
        contract_jobs.entitlement_service, "ensure_feature", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        contract_jobs.extraction_guard,
        "close_if_interrupted_after_llm",
        AsyncMock(return_value=False),
    )
    return row


@pytest.mark.asyncio
async def test_contract_worker_holds_when_ai_budget_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = AsyncMock()
    _worker_mocks(monkeypatch, db)
    finalize = AsyncMock()
    extract = AsyncMock()
    monkeypatch.setattr(
        contract_jobs.document_processing_service, "finalize_processing_attempt", finalize
    )
    monkeypatch.setattr(
        contract_jobs.document_quota_service,
        "hold_if_budget_exhausted",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(contract_jobs, "extract_contract", extract)

    result = await contract_jobs.process_contract(
        {"redis": MagicMock()}, str(uuid4()), str(uuid4())
    )

    assert result["status"] == "quota_pending"
    extract.assert_not_awaited()
    assert finalize.await_args is not None
    assert finalize.await_args.kwargs["error_code"] == DocumentErrorCode.llm_budget.value


@pytest.mark.asyncio
async def test_contract_worker_uses_plan_page_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    db = AsyncMock()
    _worker_mocks(monkeypatch, db)
    monkeypatch.setattr(
        contract_jobs.document_quota_service,
        "hold_if_budget_exhausted",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        contract_jobs, "get_storage", lambda: MagicMock(download_bytes=AsyncMock(return_value=b""))
    )
    monkeypatch.setattr(contract_jobs.extraction_guard, "mark_llm_started", AsyncMock())
    monkeypatch.setattr(
        contract_jobs.entitlement_service, "resolve_tenant", AsyncMock(return_value=MagicMock())
    )
    monkeypatch.setattr(contract_jobs.contract_quota_service, "max_pages", lambda _ents: 77)
    extract = AsyncMock(side_effect=RuntimeError("stop"))
    monkeypatch.setattr(contract_jobs, "extract_contract", extract)
    monkeypatch.setattr(contract_jobs.contract_service, "mark_failed", AsyncMock())
    monkeypatch.setattr(contract_jobs, "set_tenant_context", AsyncMock())

    await contract_jobs.process_contract({"redis": MagicMock()}, str(uuid4()), str(uuid4()))

    assert extract.await_args is not None
    assert extract.await_args.kwargs["max_pdf_pages"] == 77


def test_contract_job_timeout_is_600_seconds() -> None:
    from app.jobs.settings import WorkerSettings

    by_name = {
        getattr(fn, "name", getattr(fn, "__name__", "")): fn for fn in WorkerSettings.functions
    }
    job = by_name["process_contract"]
    assert getattr(job, "timeout_s", None) == 600


# ── Tools del chat ───────────────────────────────────────────────────────────


def test_chat_tools_exclude_replaced_contracts_unless_asked() -> None:
    search = SearchDocumentsArgs(doc_type_code="contrato")
    assert _filters_from_search_args(search).incluir_sustituidos is False
    history = SearchDocumentsArgs(doc_type_code="contrato", incluir_sustituidos=True)
    assert _filters_from_search_args(history).incluir_sustituidos is True
    aggregate = AggregateDocumentsArgs(
        doc_type_code="contrato", metric="count", incluir_sustituidos=True
    )
    assert _filters_from_aggregate_args(aggregate).incluir_sustituidos is True
    schema = SearchDocumentsArgs.model_json_schema()
    assert "renovación" in schema["properties"]["incluir_sustituidos"]["description"]


# ── Rutas ────────────────────────────────────────────────────────────────────


def _request() -> Any:
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"user-agent", b"pytest")],
            "client": ("10.0.0.1", 1234),
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["replace", "reactivate"])
async def test_lifecycle_routes_audit_commit_and_return_row(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    from app.routes.web import documents
    from fastapi.responses import HTMLResponse

    ents = object()
    service = AsyncMock()
    row = AsyncMock(return_value=HTMLResponse("row"))
    monkeypatch.setattr(
        documents.contract_quota_service,
        "mark_replaced" if action == "replace" else "reactivate",
        service,
    )
    monkeypatch.setattr(
        documents.entitlement_service, "resolve_entitlements", AsyncMock(return_value=ents)
    )
    monkeypatch.setattr(documents, "_document_row_response", row)
    db = AsyncMock()
    user = SimpleNamespace(id=uuid4())
    tenant = SimpleNamespace(id=uuid4())
    contract_id = uuid4()
    handler = (
        documents.contract_mark_replaced if action == "replace" else documents.contract_reactivate
    )

    response = await handler(_request(), contract_id, user, tenant, db)

    assert response.body == b"row"
    assert service.await_args is not None
    kwargs = service.await_args.kwargs
    assert kwargs["tenant_id"] == tenant.id
    assert kwargs["contract_id"] == contract_id
    assert kwargs["user_id"] == user.id
    assert kwargs["request_ctx"].ip == "10.0.0.1"
    # Reactivar comprueba el hueco con los entitlements del tenant.
    expected_args = (db,) if action == "replace" else (db, ents)
    assert service.await_args.args == expected_args
    db.commit.assert_awaited_once()
    assert row.await_args is not None
    assert row.await_args.kwargs == {"kind": "contract", "document_id": contract_id}
