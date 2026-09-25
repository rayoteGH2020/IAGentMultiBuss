"""Tests Paso07: no-fuga en tools documentales y allowlist por entitlements."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.llm.tools.document_chat import GetDocumentArgs, execute_get_document
from app.llm.tools.registry import ToolContext, ToolFamily, chat_tool_families_for_entitlements
from app.models import Tenant
from app.schemas.entitlements import Entitlements
from app.schemas.invoice import Factura
from app.services import invoice_service

_FORBIDDEN_KEYS = frozenset(
    {
        "raw_extraction",
        "source_file_key",
        "error_code",
        "llm_call_id",
    },
)


def _assert_no_forbidden_keys(payload: object) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key not in _FORBIDDEN_KEYS, f"leaked key: {key}"
            _assert_no_forbidden_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_forbidden_keys(item)


def test_chat_tool_families_fail_closed_without_has() -> None:
    assert chat_tool_families_for_entitlements(object()) == frozenset()


def test_chat_tool_families_respect_entitlements() -> None:
    docs_only = Entitlements(
        plan_code="basic",
        features=frozenset({"documents_chat"}),
        limits={},
    )
    assert chat_tool_families_for_entitlements(docs_only) == frozenset({ToolFamily.document})

    knowledge_only = Entitlements(
        plan_code="pro",
        features=frozenset({"knowledge_chat"}),
        limits={},
    )
    assert chat_tool_families_for_entitlements(knowledge_only) == frozenset(
        {ToolFamily.knowledge},
    )

    empty = Entitlements(plan_code="free", features=frozenset(), limits={})
    assert chat_tool_families_for_entitlements(empty) == frozenset()


@pytest.mark.asyncio
async def test_get_document_tool_omits_sensitive_fields(
    invoices_schema_ready: None,
    db_session,
    tenant_factory,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    inv = await invoice_service.create_invoice_stub(
        db_session,
        tenant.id,
        source_file_key=f"secret/r2/{uuid4().hex}.pdf",
        source_filename="inv.pdf",
        source_mime="application/pdf",
    )
    inv = await invoice_service.get_invoice(db_session, tenant.id, inv.id)
    factura = Factura(
        fecha=date(2025, 4, 10),
        proveedor="Proveedor Seguro",
        cif_nif="B12345678",  # pragma: allowlist secret
        base_imponible=Decimal("100"),
        iva_percent=Decimal("21"),
        iva_amount=Decimal("21"),
        total=Decimal("121"),
        confidence=0.95,
    )
    await invoice_service.apply_extraction_result(
        db_session,
        invoice=inv,
        factura=factura,
        llm_call_id=uuid4(),
    )
    await db_session.flush()

    # Contamina raw_extraction para asegurar que no se proyecta.
    inv.raw_extraction = {"secret": "should-not-leak", "proveedor": "x"}  # pragma: allowlist secret
    await db_session.flush()

    ctx = ToolContext(db=db_session, tenant_id=tenant.id)
    result = await execute_get_document(
        ctx,
        GetDocumentArgs(doc_type_code="factura", document_id=inv.id),
    )
    assert result.ok is True
    data = cast(dict[str, Any], result.data)
    _assert_no_forbidden_keys(data)
    assert "document" in data
    assert data["document"]["proveedor"] == "Proveedor Seguro"
