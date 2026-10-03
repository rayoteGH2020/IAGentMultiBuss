"""Bloque 7: niveles de aviso y plantillas de consumo (panel, chat y SADM)."""

from __future__ import annotations

import re
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.core.templating import templates
from app.services.quota_status_service import QuotaAlert, QuotaStatus


@pytest.mark.parametrize(
    ("used", "cap", "level", "percent"),
    [
        (0, 40, None, 0),
        (31, 40, None, 78),
        (32, 40, "warning", 80),
        (39, 40, "warning", 98),
        (40, 40, "exhausted", 100),
        (45, 40, "exhausted", 100),
        (3, None, None, None),
        (0, 0, None, None),  # tope 0 = no incluido: sin barra ni aviso
    ],
)
def test_levels_and_percent(
    used: int, cap: int | None, level: str | None, percent: int | None
) -> None:
    status = QuotaStatus(key="documents", label="x", used=used, cap=cap)
    assert status.level == level
    assert status.percent == percent


def test_renewal_label_in_spanish() -> None:
    status = QuotaStatus(key="chat", label="x", used=1, cap=2, renewal=date(2026, 11, 1))
    assert status.renewal_label == "1 de noviembre"


def _compact(html: str) -> str:
    return re.sub(r"\s+", " ", html)


def test_alerts_component_colors_by_level() -> None:
    template = templates.env.get_template("components/quota_alerts.html")
    html = _compact(
        template.render(
            quota_alerts=[
                QuotaAlert("warning", "Has usado 32 de 40"),
                QuotaAlert("exhausted", "Has agotado"),
            ]
        )
    )
    assert 'data-quota-alert="warning"' in html and "bg-amber-50" in html
    assert 'data-quota-alert="exhausted"' in html and "bg-red-50" in html
    assert "Has usado 32 de 40" in html
    assert template.render(quota_alerts=[]).strip() == ""


def test_documents_panel_renders_quota_alerts() -> None:
    template = templates.env.get_template("components/invoices_panel.html")
    html = template.render(
        quota_alerts=[QuotaAlert("exhausted", "Has agotado las 40 facturas y tickets")],
        documents=[],
        doc_types=[],
        upload_errors=[],
    )
    assert "Has agotado las 40 facturas y tickets" in html


def test_sadm_tenant_list_marks_quota_levels() -> None:
    template = templates.env.get_template("pages/sadm/plans/_tenants.html")
    warn, full, ok = uuid4(), uuid4(), uuid4()
    tenants = [
        SimpleNamespace(id=tid, name=name, plan_code="basic", plan="basic")
        for tid, name in ((warn, "Aviso"), (full, "Lleno"), (ok, "Normal"))
    ]
    html = template.render(
        tenants=tenants, alert_levels={warn: "warning", full: "exhausted", ok: None}
    )
    assert html.count('data-quota-level="warning"') == 1
    assert html.count('data-quota-level="exhausted"') == 1
    assert "≥ 80 %" in html and "100 %" in html
