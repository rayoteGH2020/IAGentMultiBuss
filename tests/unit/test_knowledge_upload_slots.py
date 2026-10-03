"""Subida a /knowledge por zonas: una categoría por fichero, como /documents."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.core.errors import ValidationError
from app.core.templating import templates
from app.routes.web import knowledge as knowledge_routes
from app.schemas.knowledge import KnowledgeDocumentKind
from app.services import knowledge_document_service
from fastapi import UploadFile
from starlette.datastructures import Headers

_ROOT = Path(__file__).resolve().parents[2]
_JS = _ROOT / "app" / "static" / "js" / "alpine-components.js"


def test_resolve_per_file_kinds_keeps_order() -> None:
    kinds = knowledge_document_service.resolve_per_file_kinds(
        file_count=3, kinds=["faq", "schedule", "faq"]
    )
    assert kinds == [
        KnowledgeDocumentKind.faq,
        KnowledgeDocumentKind.schedule,
        KnowledgeDocumentKind.faq,
    ]


def test_resolve_per_file_kinds_accepts_single_string() -> None:
    assert knowledge_document_service.resolve_per_file_kinds(file_count=1, kinds="policy") == [
        KnowledgeDocumentKind.policy
    ]


@pytest.mark.parametrize(
    ("file_count", "kinds"),
    [
        (2, ["faq"]),  # falta una categoría
        (1, ["faq", "policy"]),  # sobra una
        (1, None),
        (1, [""]),
        (1, ["invoice"]),  # tipo de /documents, no de knowledge
    ],
)
def test_resolve_per_file_kinds_rejects_invalid(file_count: int, kinds: Any) -> None:
    with pytest.raises(ValidationError):
        knowledge_document_service.resolve_per_file_kinds(file_count=file_count, kinds=kinds)


def _upload(name: str) -> UploadFile:
    return UploadFile(
        file=BytesIO(b"hola"), filename=name, headers=Headers({"content-type": "text/plain"})
    )


def _patch_route(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, dict[str, Any]]:
    captured: dict[str, Any] = {}

    def fake_response(_request: object, _ctx: object, *, created: int, errors: list) -> str:
        captured.update(created=created, errors=errors)
        return "response"

    create = AsyncMock(side_effect=lambda *_a, **_kw: SimpleNamespace(id=uuid4()))
    monkeypatch.setattr(knowledge_routes, "_knowledge_upload_response", fake_response)
    monkeypatch.setattr(knowledge_routes, "_list_ctx", AsyncMock(return_value={}))
    monkeypatch.setattr(knowledge_routes, "read_upload_limited", AsyncMock(return_value=b"x"))
    monkeypatch.setattr(knowledge_routes, "enqueue_knowledge_indexing", AsyncMock())
    monkeypatch.setattr(knowledge_routes.entitlement_service, "resolve_entitlements", AsyncMock())
    monkeypatch.setattr(knowledge_routes.plan_quota_service, "ensure_knowledge_upload", AsyncMock())
    monkeypatch.setattr(knowledge_routes.knowledge_document_service, "create_from_upload", create)
    return create, captured


async def _post(files: list[UploadFile], kinds: Any) -> None:
    await knowledge_routes.upload_knowledge(
        MagicMock(),
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4()),
        AsyncMock(),
        kinds=kinds,
        files=files,
        db=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_upload_assigns_each_file_its_own_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create, captured = _patch_route(monkeypatch)

    await _post([_upload("horario.txt"), _upload("faq.md")], ["schedule", "faq"])

    assert [(c.kwargs["filename"], c.kwargs["kind"]) for c in create.await_args_list] == [
        ("horario.txt", KnowledgeDocumentKind.schedule),
        ("faq.md", KnowledgeDocumentKind.faq),
    ]
    assert captured == {"created": 2, "errors": []}


@pytest.mark.asyncio
async def test_upload_rejects_batch_without_category_per_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create, captured = _patch_route(monkeypatch)

    await _post([_upload("a.txt"), _upload("b.txt")], ["faq"])

    create.assert_not_awaited()
    assert captured["created"] == 0
    assert captured["errors"][0]["error"] == "Debes indicar la categoría de cada documento."


@pytest.mark.asyncio
async def test_upload_limit_matches_documents_10_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create, captured = _patch_route(monkeypatch)

    await _post([_upload(f"{i}.txt") for i in range(11)], ["faq"] * 11)

    create.assert_not_awaited()
    assert captured["errors"][0]["error"] == "Máximo 10 ficheros por subida."


def _render_modal(template: str, **ctx: object) -> str:
    return templates.env.get_template(f"components/{template}").render(**ctx)


def test_knowledge_modal_uses_slots_with_category_per_file() -> None:
    html = _render_modal("knowledge_upload_form.html")

    assert 'x-data="knowledgeUploadForm"' in html
    assert 'x-for="slot in slots"' in html
    assert 'name="kinds"' in html
    assert 'name="kind"' not in html
    for label in ("Contrato", "Horarios", "Preguntas frecuentes", "Otro"):
        assert f">{label}</option>" in html
    assert 'hx-post="/knowledge/upload"' in html
    assert "15 MB máx. por fichero" in html
    assert ".pdf,.txt,.md,.markdown,.jpg,.jpeg,.png,.webp" in html


def test_documents_modal_still_uses_doc_type_per_slot() -> None:
    doc_types = [SimpleNamespace(code="invoice", name="Factura")]
    html = _render_modal("upload_modal.html", doc_types=doc_types)

    assert 'x-for="slot in slots"' in html
    assert 'name="doc_type_codes"' in html
    assert '<option value="invoice">Factura</option>' in html
    assert 'hx-post="/documents/upload"' in html


def test_both_forms_share_the_slot_component() -> None:
    js = _JS.read_text(encoding="utf-8")
    assert 'typeField: "kinds"' in js
    assert 'typeField: "doc_type_codes"' in js
    assert js.count("function createSlotUploadForm(") == 1
