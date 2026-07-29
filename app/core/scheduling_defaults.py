"""Defaults de scheduling (Paso 30) — usados en migración y servicios."""

from __future__ import annotations

from typing import Any, TypedDict

from app.core.scheduling_granularity import DEFAULT_SLOT_GRANULARITY_MINUTES


class BusinessHourSeed(TypedDict):
    weekday: int
    sort_order: int
    opens_at: str
    closes_at: str


DEFAULT_MEMBERSHIP_PERMISSIONS: dict[str, Any] = {
    "appointments": {
        "view": True,
        "create": False,
        "edit": False,
        "cancel": False,
    },
}

DEFAULT_SCHEDULING_SETTINGS: dict[str, Any] = {
    "timezone": "Europe/Madrid",
    "search_horizon_days": 14,
    "slot_granularity_minutes": DEFAULT_SLOT_GRANULARITY_MINUTES,
    "buffer_minutes": 10,
}

# Lunes-viernes (0-4): manana 09:00-14:00, tarde 16:00-21:00 (decision 21).
_DEFAULT_MORNING: list[BusinessHourSeed] = [
    {"weekday": day, "sort_order": 0, "opens_at": "09:00", "closes_at": "14:00"} for day in range(5)
]
_DEFAULT_AFTERNOON: list[BusinessHourSeed] = [
    {"weekday": day, "sort_order": 1, "opens_at": "16:00", "closes_at": "21:00"} for day in range(5)
]
DEFAULT_BUSINESS_HOUR_SEEDS: tuple[BusinessHourSeed, ...] = tuple(
    _DEFAULT_MORNING + _DEFAULT_AFTERNOON
)
