"""Periodo de cupos y presupuesto: mes natural en hora de España (D027)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from app.core.billing_period import (
    current_period_start,
    initial_load_window_end,
    local_date,
    next_period_start,
    period_end,
    period_start,
)
from app.core.entitlement_codes import (
    LIMIT_CHAT_QUESTIONS_PER_MONTH,
    LIMIT_INVOICES_PER_MONTH,
    LIMIT_TICKETS_PER_MONTH,
    MONTHLY_QUOTA_CODES,
    MONTHLY_QUOTA_UI_LABELS,
    PLAN_LIMITS,
    monthly_quota_bag,
)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2028, 2, 1), date(2028, 2, 29)),
        (date(2026, 8, 1), date(2026, 8, 31)),
        (date(2026, 11, 1), date(2026, 11, 30)),
    ],
)
def test_period_end_is_last_day_of_month(start: date, end: date) -> None:
    assert period_end(start) == end


def test_next_period_start_rolls_year() -> None:
    assert next_period_start(date(2026, 12, 1)) == date(2027, 1, 1)
    assert next_period_start(date(2026, 10, 1)) == date(2026, 11, 1)


def test_period_start_is_first_day() -> None:
    assert period_start(date(2026, 10, 15)) == date(2026, 10, 1)


def test_current_period_uses_spain_time_at_month_boundary() -> None:
    # 31/10 23:30 UTC = 01/11 00:30 en Madrid (CET): ya es noviembre.
    assert current_period_start(datetime(2026, 10, 31, 23, 30, tzinfo=UTC)) == date(2026, 11, 1)
    # 31/08 21:59 UTC = 31/08 23:59 en Madrid (CEST): sigue siendo agosto.
    assert current_period_start(datetime(2026, 8, 31, 21, 59, tzinfo=UTC)) == date(2026, 8, 1)


def test_local_date_treats_naive_as_utc() -> None:
    assert local_date(datetime(2026, 10, 31, 23, 30)) == date(2026, 11, 1)


@pytest.mark.parametrize(
    ("created_at", "window_end"),
    [
        # Alta el día 1 (hora de España): la ventana es ese mes.
        (datetime(2026, 10, 1, 8, 0, tzinfo=UTC), date(2026, 10, 31)),
        # 30/09 22:30 UTC = 01/10 00:30 en Madrid: cuenta como alta el día 1.
        (datetime(2026, 9, 30, 22, 30, tzinfo=UTC), date(2026, 10, 31)),
        # Alta a mitad o final de mes: hasta el final del primer mes completo.
        (datetime(2026, 10, 28, 10, 0, tzinfo=UTC), date(2026, 11, 30)),
        (datetime(2026, 12, 15, 10, 0, tzinfo=UTC), date(2027, 1, 31)),
        (datetime(2027, 1, 31, 10, 0, tzinfo=UTC), date(2027, 2, 28)),
    ],
)
def test_initial_load_window_end(created_at: datetime, window_end: date) -> None:
    assert initial_load_window_end(created_at) == window_end


def test_invoices_and_tickets_share_a_bag() -> None:
    bag = monthly_quota_bag(LIMIT_INVOICES_PER_MONTH)
    assert bag == monthly_quota_bag(LIMIT_TICKETS_PER_MONTH)
    assert set(bag) == {LIMIT_INVOICES_PER_MONTH, LIMIT_TICKETS_PER_MONTH}
    assert monthly_quota_bag(LIMIT_CHAT_QUESTIONS_PER_MONTH) == (LIMIT_CHAT_QUESTIONS_PER_MONTH,)


def test_every_monthly_quota_has_label_and_seed_value() -> None:
    assert set(MONTHLY_QUOTA_UI_LABELS) == MONTHLY_QUOTA_CODES
    for limits in PLAN_LIMITS.values():
        assert set(limits) >= MONTHLY_QUOTA_CODES
