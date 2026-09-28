"""/settings/profile: organización con nombre y resumen del plan efectivo."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.core.entitlement_codes import PLAN_FEATURES, PLAN_LIMITS
from app.core.templating import templates
from app.schemas.entitlements import Entitlements, PlanLimitItem, PlanSummary
from app.services.entitlement_service import build_plan_summary


def _ents(plan_code: str, **limit_overrides: Decimal | None) -> Entitlements:
    limits = dict(PLAN_LIMITS[plan_code])
    limits.update(limit_overrides)
    return Entitlements(plan_code=plan_code, features=PLAN_FEATURES[plan_code], limits=limits)


def _limits(summary: PlanSummary) -> dict[str, str]:
    return {item.label: item.value for item in summary.limits}


def test_basic_plan_summary_lists_features_and_nonzero_limits() -> None:
    summary = build_plan_summary(_ents("basic"))

    assert summary.name == "Básico"
    assert summary.features == [
        "Documentos",
        "Chat documental",
        "Base de conocimiento",
        "Chat sobre conocimiento",
    ]
    limits = _limits(summary)
    assert limits["Documentos procesados al día"] == "50"
    assert limits["Miembros del equipo"] == "5"
    # Límites a 0 = prestación no incluida: no se listan.
    assert "Canales de mensajería conectados" not in limits
    assert "Mensajes por cliente y hora en canales" not in limits


def test_premium_plan_summary_formats_thousands_and_includes_channels() -> None:
    summary = build_plan_summary(_ents("premium"))

    assert summary.name == "Premium"
    assert {"Citas", "WhatsApp", "Telegram"} <= set(summary.features)
    limits = _limits(summary)
    assert limits["Documentos en la base de conocimiento"] == "1.500"
    assert limits["Canales de mensajería conectados"] == "2"


def test_summary_hides_internal_limits_and_calendar() -> None:
    summary = build_plan_summary(_ents("advanced"))

    labels = " ".join(item.label for item in summary.limits).lower()
    assert "presupuesto" not in labels and "eur" not in labels  # tope interno de coste
    assert "voz" not in labels
    assert "Calendario Google" not in summary.features


def test_summary_reflects_sadm_override() -> None:
    ents = _ents("basic", members_max=None)
    ents = ents.model_copy(update={"features": ents.features | {"appointments"}})

    summary = build_plan_summary(ents)

    assert "Citas" in summary.features
    assert _limits(summary)["Miembros del equipo"] == "Ilimitado"


def test_fail_closed_summary_has_no_features() -> None:
    summary = build_plan_summary(Entitlements(plan_code="basic", fail_closed=True))
    assert summary.features == [] and summary.limits == []


def _usage(*, budget: Decimal | None = Decimal("30")) -> SimpleNamespace:
    return SimpleNamespace(
        plan_code="basic",
        period=date(2026, 9, 1),
        llm_cost_eur=Decimal("1.5"),
        llm_budget_eur=budget,
        rag_messages_count=3,
    )


def _render_org_section(plan: PlanSummary, usage: SimpleNamespace | None = None) -> str:
    template = templates.env.get_template("pages/settings/profile.html")
    ctx = template.new_context(
        {
            "user": SimpleNamespace(name="Ana", email="ana@example.com", created_at=None),
            "tenant": SimpleNamespace(name="Peluqueria The Moon"),
            "plan": plan,
            "usage": usage,
        }
    )
    return "".join(template.blocks["settings_content"](ctx))


def _compact(html: str) -> str:
    return re.sub(r"\s+", " ", html)


def test_profile_includes_monthly_usage_formerly_in_billing() -> None:
    plan = PlanSummary(code="basic", name="Básico", features=["Documentos"], limits=[])
    html = _compact(_render_org_section(plan, _usage()))

    assert "Consumo del mes" in html and "(09/2026)" in html
    assert "1,50 € <span" in html and "de 30,00 €" in html
    assert "Consultas a la base de conocimiento" in html
    # Sin duplicados: el plan solo aparece en la cabecera de la tarjeta.
    assert "Plan actual" not in html
    assert html.count("Básico") == 1


def test_profile_usage_without_budget_shows_no_cap() -> None:
    plan = PlanSummary(code="premium", name="Premium", features=[], limits=[])
    html = _compact(_render_org_section(plan, _usage(budget=None)))
    assert "(sin tope)" in html


def test_profile_shows_org_name_plan_and_list_without_members_card() -> None:
    plan = PlanSummary(
        code="basic",
        name="Básico",
        features=["Documentos", "Chat documental"],
        limits=[PlanLimitItem(label="Miembros del equipo", value="5")],
    )
    html = _render_org_section(plan)

    assert "Peluqueria The Moon" in html
    assert "Básico" in html
    assert "Documentos</li>" in html.replace("\n", "").replace("  ", "")
    compact = _compact(html)
    assert '"text-sm text-slate-600">Miembros del equipo</span>' in compact
    assert 'tabular-nums"> 5 </span>' in compact
    assert "Miembros del equipo</h3>" not in html
    assert "/settings/members" not in html
    assert "<form" not in html


def test_profile_without_active_features_shows_notice() -> None:
    html = _render_org_section(PlanSummary(code="basic", name="Básico", features=[], limits=[]))
    assert "No hay funcionalidades activas" in html
