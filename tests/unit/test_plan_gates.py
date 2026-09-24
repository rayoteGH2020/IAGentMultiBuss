"""Gates por feature (Paso03): nav, require_feature y workers/webhooks."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.core.entitlement_codes import FEATURE_KNOWLEDGE
from app.core.errors import PlanRequiredError
from app.core.permissions import nav_items_for_access
from app.schemas.entitlements import Entitlements


def test_nav_hides_channels_for_basic() -> None:
    ents = Entitlements(
        plan_code="basic",
        features=frozenset({"documents", "documents_chat", "knowledge", "knowledge_chat"}),
        limits={},
    )
    hrefs = {h for h, _, _ in nav_items_for_access("admin", ents)}
    assert "/documents" in hrefs
    assert "/chat" in hrefs
    assert "/knowledge" in hrefs
    assert "/appointments" not in hrefs
    assert "/calendar" not in hrefs
    assert "/settings" in hrefs


def test_nav_basic_shows_knowledge() -> None:
    ents = Entitlements(
        plan_code="basic",
        features=frozenset({"documents", "documents_chat", "knowledge", "knowledge_chat"}),
        limits={},
    )
    hrefs = {h for h, _, _ in nav_items_for_access("admin", ents)}
    assert "/knowledge" in hrefs
    assert "/appointments" not in hrefs


def test_nav_member_hides_appointments_without_feature() -> None:
    ents = Entitlements(
        plan_code="basic",
        features=frozenset({"documents_chat"}),
        limits={},
    )
    hrefs = {h for h, _, _ in nav_items_for_access("member", ents)}
    assert hrefs == {"/chat"}


def test_sidebar_uses_nav_items_for_access() -> None:
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[2] / "app" / "templates" / "components" / "sidebar.html"
    ).read_text(encoding="utf-8")
    assert "nav_items_for_access" in html


def test_plan_required_error_code() -> None:
    exc = PlanRequiredError(FEATURE_KNOWLEDGE)
    assert exc.code == "plan_required"
    assert exc.feature == FEATURE_KNOWLEDGE
    assert exc.status_code == 403


@pytest.mark.asyncio
async def test_worker_skips_llm_when_documents_feature_off() -> None:
    from app.jobs import invoice_jobs

    invoice_id = str(uuid4())
    tenant_id = str(uuid4())
    inv = MagicMock()
    inv.source_file_key = "invoices/x.pdf"
    inv.source_mime = "application/pdf"
    inv.source_filename = "x.pdf"

    db = AsyncMock()
    extract = AsyncMock()

    with (
        patch.object(invoice_jobs, "tenant_invoice_extraction_slot") as slot_cm,
        patch.object(invoice_jobs, "session_factory_for_worker") as sess_cm,
        patch.object(invoice_jobs.invoice_service, "get_invoice", AsyncMock(return_value=inv)),
        patch.object(
            invoice_jobs.document_processing_service,
            "begin_processing_attempt",
            AsyncMock(),
        ),
        patch.object(
            invoice_jobs.entitlement_service,
            "ensure_feature",
            AsyncMock(return_value=False),
        ),
        patch.object(invoice_jobs.invoice_service, "mark_failed", AsyncMock()) as mark_failed,
        patch.object(invoice_jobs, "extract_invoice", extract),
        patch.object(invoice_jobs, "get_redis", MagicMock()),
    ):
        slot_cm.return_value.__aenter__ = AsyncMock(return_value=None)
        slot_cm.return_value.__aexit__ = AsyncMock(return_value=None)
        sess_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        sess_cm.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await invoice_jobs.process_invoice({}, invoice_id, tenant_id)

    assert result["status"] == "skipped"
    assert result["reason"] == "plan_required"
    extract.assert_not_awaited()
    mark_failed.assert_awaited()


def test_entitlements_has_null_limit_unlimited() -> None:
    ents = Entitlements(
        plan_code="premium",
        features=frozenset(),
        limits={"llm_budget_eur_month": None},
    )
    assert ents.limit("llm_budget_eur_month") is None
    assert ents.limit("documents_per_day") == Decimal("0")
