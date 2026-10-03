"""Unit tests for chat usage period windows (SADM)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import patch
from uuid import uuid4

from app.services.chat_usage_service import (
    DAY_WINDOW,
    PERIOD_WINDOW,
    ChatUsageTenantItem,
    _iter_buckets,
    resolve_period,
    tenant_picker_options,
)


def test_tenant_picker_options_serializes_id_name_plan() -> None:
    tid = uuid4()
    tenants = [
        ChatUsageTenantItem(
            id=tid,
            name="Acme",
            plan="pro",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    ]
    assert tenant_picker_options(tenants) == [
        {"id": str(tid), "name": "Acme", "plan": "pro"},
    ]


def test_week_window_is_six_weeks_ending_at_current() -> None:
    today = date(2026, 7, 28)  # Tuesday → week starts 2026-07-27
    with patch("app.services.chat_usage_service._today", return_value=today):
        period = resolve_period(view="week", anchor=today)
        buckets = _iter_buckets(period)

    assert period.anchor == date(2026, 7, 27)
    assert period.start == date(2026, 6, 22)  # 5 weeks before current Monday
    assert period.end_exclusive == date(2026, 8, 3)
    assert len(buckets) == PERIOD_WINDOW
    assert buckets[0].start == date(2026, 6, 22)
    assert buckets[-1].start == date(2026, 7, 27)
    assert period.can_go_forward is False
    assert period.next_anchor is None
    assert period.prev_anchor == date(2026, 6, 15)  # end - 6 weeks


def test_week_prev_next_moves_by_six_weeks() -> None:
    today = date(2026, 7, 28)
    with patch("app.services.chat_usage_service._today", return_value=today):
        past = resolve_period(view="week", anchor=date(2026, 6, 15))
    assert past.anchor == date(2026, 6, 15)
    assert past.start == date(2026, 5, 11)
    assert past.can_go_forward is True
    assert past.next_anchor == date(2026, 7, 27)  # clamped to current week
    assert past.prev_anchor == date(2026, 5, 4)


def test_month_window_is_six_months_aggregated() -> None:
    today = date(2026, 7, 28)
    with patch("app.services.chat_usage_service._today", return_value=today):
        period = resolve_period(view="month", anchor=date(2026, 8, 1))
        buckets = _iter_buckets(period)

    assert period.anchor == date(2026, 7, 1)
    assert period.start == date(2026, 2, 1)
    assert period.end_exclusive == date(2026, 8, 1)
    assert period.can_go_forward is False
    assert [b.label for b in buckets] == [
        "2026-02",
        "2026-03",
        "2026-04",
        "2026-05",
        "2026-06",
        "2026-07",
    ]
    assert period.prev_anchor == date(2026, 1, 1)


def test_month_prev_next_moves_by_six_months() -> None:
    today = date(2026, 7, 28)
    with patch("app.services.chat_usage_service._today", return_value=today):
        past = resolve_period(view="month", anchor=date(2026, 1, 1))
    assert past.anchor == date(2026, 1, 1)
    assert past.start == date(2025, 8, 1)
    assert past.can_go_forward is True
    assert past.next_anchor == date(2026, 7, 1)  # clamped to current month
    assert past.prev_anchor == date(2025, 7, 1)


def test_day_window_is_seven_days_ending_at_today() -> None:
    today = date(2026, 7, 28)
    with patch("app.services.chat_usage_service._today", return_value=today):
        period = resolve_period(view="day", anchor=date(2026, 7, 30))
        buckets = _iter_buckets(period)

    assert period.anchor == today
    assert period.start == date(2026, 7, 22)
    assert period.end_exclusive == date(2026, 7, 29)
    assert len(buckets) == DAY_WINDOW
    assert buckets[0].label == "2026-07-22"
    assert buckets[-1].label == "2026-07-28"
    assert period.can_go_forward is False
    assert period.next_anchor is None
    assert period.prev_anchor == date(2026, 7, 21)


def test_day_prev_next_moves_by_seven_days() -> None:
    today = date(2026, 7, 28)
    with patch("app.services.chat_usage_service._today", return_value=today):
        past = resolve_period(view="day", anchor=date(2026, 7, 21))
    assert past.anchor == date(2026, 7, 21)
    assert past.start == date(2026, 7, 15)
    assert past.can_go_forward is True
    assert past.next_anchor == today  # clamped to today
    assert past.prev_anchor == date(2026, 7, 14)
