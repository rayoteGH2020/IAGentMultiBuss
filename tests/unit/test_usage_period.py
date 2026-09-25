"""Ventanas mensuales usadas para agregar consumo por tenant."""

from __future__ import annotations

from datetime import date

from app.services.usage_service import next_period


def test_next_period_rolls_over_the_year() -> None:
    assert next_period(date(2026, 12, 1)) == date(2027, 1, 1)


def test_next_period_advances_one_month() -> None:
    assert next_period(date(2026, 3, 1)) == date(2026, 4, 1)
