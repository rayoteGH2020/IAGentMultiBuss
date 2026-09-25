"""Tests puros de detección de processing huérfano."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.document_processing_service import is_processing_stale


def test_is_processing_stale_false_when_recent() -> None:
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    started = now - timedelta(minutes=5)
    assert not is_processing_stale(started, now=now, stale_after_seconds=900)


def test_is_processing_stale_true_when_past_threshold() -> None:
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    started = now - timedelta(minutes=16)
    assert is_processing_stale(started, now=now, stale_after_seconds=900)


def test_is_processing_stale_true_at_default_three_minutes() -> None:
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    started = now - timedelta(minutes=3)
    assert is_processing_stale(started, now=now, stale_after_seconds=180)


def test_is_processing_stale_false_for_none_or_disabled() -> None:
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    assert not is_processing_stale(None, now=now, stale_after_seconds=900)
    started = now - timedelta(hours=2)
    assert not is_processing_stale(started, now=now, stale_after_seconds=0)


def test_is_processing_stale_accepts_naive_datetime_as_utc() -> None:
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    started = datetime(2026, 7, 29, 11, 0)  # naive
    assert is_processing_stale(started, now=now, stale_after_seconds=900)
