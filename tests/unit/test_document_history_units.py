"""Histórico visible (D017, bloque 6): fecha de corte, límite del plan y overrides."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.core.billing_period import history_visible_from, months_before
from app.core.entitlement_codes import LIMIT_HISTORY_MONTHS, LIMIT_UI_LABELS, PLAN_LIMITS
from app.schemas.entitlements import Entitlements, EntitlementsOverride
from app.services import document_history_service, document_quota_service
from pydantic import ValidationError


@pytest.mark.parametrize(
    ("today", "months", "expected"),
    [
        (date(2026, 10, 3), 12, date(2025, 10, 1)),
        (date(2026, 1, 31), 1, date(2025, 12, 1)),
        (date(2026, 3, 15), 36, date(2023, 3, 1)),
        (date(2026, 12, 31), 12, date(2025, 12, 1)),
    ],
)
def test_visible_from_counts_whole_months(today: date, months: int, expected: date) -> None:
    assert history_visible_from(months, today) == expected


def test_months_before_rolls_years() -> None:
    assert months_before(date(2026, 2, 1), 2) == date(2025, 12, 1)
    assert months_before(date(2026, 2, 1), 0) == date(2026, 2, 1)


def test_catalog_values_and_label() -> None:
    assert PLAN_LIMITS["basic"][LIMIT_HISTORY_MONTHS] == Decimal("12")
    assert PLAN_LIMITS["advanced"][LIMIT_HISTORY_MONTHS] == Decimal("36")
    assert PLAN_LIMITS["premium"][LIMIT_HISTORY_MONTHS] is None
    assert LIMIT_HISTORY_MONTHS in LIMIT_UI_LABELS


def _ents(limits: dict[str, Decimal | None]) -> Entitlements:
    return Entitlements(plan_code="basic", features=frozenset(), limits=limits)


def test_history_months_from_entitlements() -> None:
    assert (
        document_history_service.history_months(_ents({LIMIT_HISTORY_MONTHS: Decimal("12")})) == 12
    )
    assert document_history_service.history_months(_ents({LIMIT_HISTORY_MONTHS: None})) is None
    assert document_history_service.visible_from_for(_ents({LIMIT_HISTORY_MONTHS: None})) is None
    # Sin el límite en el catálogo no se oculta todo: se aplica el del plan más bajo.
    assert document_history_service.history_months(_ents({})) == 12


def test_override_rejects_zero_history_but_accepts_null() -> None:
    with pytest.raises(ValidationError, match="history_months"):
        EntitlementsOverride(limits={LIMIT_HISTORY_MONTHS: Decimal("0")})
    assert EntitlementsOverride(limits={LIMIT_HISTORY_MONTHS: None}).limits == {
        LIMIT_HISTORY_MONTHS: None
    }
    assert EntitlementsOverride(limits={LIMIT_HISTORY_MONTHS: Decimal("6")})


def test_duplicate_message_explains_hidden_original() -> None:
    match = document_quota_service.DuplicateMatch(
        kind="invoice",
        document_id=uuid4(),
        status="ready",
        created_at=datetime(2024, 5, 1, tzinfo=UTC),
        hidden_by_history=True,
    )
    message = document_quota_service.duplicate_message(match, filename="f.pdf", history_months=12)
    assert "ya está subido como factura" in message
    assert "fuera de los 12 meses de histórico de tu plan" in message
