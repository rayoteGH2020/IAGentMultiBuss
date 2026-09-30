"""Estados internos en inglés, etiquetas en español en la UI (filtro ``status_label``)."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import pytest
from app.core.status_labels import STATUS_LABELS, status_label
from app.core.templating import templates
from app.models.contract import ContractStatus
from app.models.insurance import InsuranceStatus
from app.models.invoice import InvoiceStatus
from app.models.ticket import TicketStatus
from app.schemas.knowledge import KnowledgeDocumentStatus
from app.schemas.scheduling import AppointmentStatus

_TEMPLATES = Path("app/templates/components")


@pytest.mark.parametrize(
    "enum_cls",
    [
        InvoiceStatus,
        TicketStatus,
        ContractStatus,
        InsuranceStatus,
        KnowledgeDocumentStatus,
        AppointmentStatus,
    ],
)
def test_every_user_visible_status_has_a_spanish_label(enum_cls: type[StrEnum]) -> None:
    missing = [member.value for member in enum_cls if member.value not in STATUS_LABELS]
    assert missing == []


def test_status_label_accepts_enum_string_none_and_unknown() -> None:
    assert status_label(InvoiceStatus.pending) == "Pendiente"
    assert status_label("processing") == "Procesando"
    assert status_label(AppointmentStatus.no_show) == "No se presentó"
    assert status_label("ok") == "Correcta"
    assert status_label(None) == ""
    assert status_label("something_new") == "something_new"


def test_status_label_is_registered_as_jinja_filter() -> None:
    rendered = templates.env.from_string("{{ s | status_label }}").render(
        s=KnowledgeDocumentStatus.indexing
    )
    assert rendered == "Indexando"


@pytest.mark.parametrize(
    ("template", "raw"),
    [
        ("document_row.html", "{{ document.status }}</span>"),
        ("llm_call_details.html", "{{ call.status }}</span>"),
        ("scheduling/appointment_form.html", "{{ st.value }}</option>"),
        ("knowledge_rows.html", "status_labels.get("),
    ],
)
def test_templates_show_translated_status_not_raw_value(template: str, raw: str) -> None:
    source = (_TEMPLATES / template).read_text(encoding="utf-8")

    assert raw not in source
    assert "status_label" in source


@pytest.mark.parametrize("template", ["knowledge_row.html", "knowledge_detail_panel.html"])
def test_knowledge_status_texts_come_from_the_filter(template: str) -> None:
    source = (_TEMPLATES / template).read_text(encoding="utf-8")

    assert source.count("{{ status | status_label }}") == 4
