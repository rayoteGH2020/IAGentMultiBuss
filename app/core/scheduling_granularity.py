"""Granularidad de tramos de citas (Paso 30)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.errors import ValidationError

if TYPE_CHECKING:
    from datetime import datetime

DEFAULT_SLOT_GRANULARITY_MINUTES = 15


def slot_minute_options(granularity_minutes: int) -> list[str]:
    """Minutos válidos en una hora para la granularidad dada (00-59)."""
    if granularity_minutes <= 0 or granularity_minutes > 60:
        raise ValueError("granularity_minutes must be between 1 and 60")
    return [f"{minute:02d}" for minute in range(0, 60, granularity_minutes)]


def validate_datetime_granularity(dt: datetime, granularity_minutes: int) -> None:
    """Comprueba que ``dt`` cae en un límite de tramo configurado."""
    if granularity_minutes <= 0:
        raise ValidationError("slot_granularity_minutes must be positive")
    if dt.second != 0 or dt.microsecond != 0:
        raise ValidationError(f"datetime must align to {granularity_minutes}-minute boundaries")
    total_minutes = dt.hour * 60 + dt.minute
    if total_minutes % granularity_minutes != 0:
        raise ValidationError(f"datetime must align to {granularity_minutes}-minute boundaries")


def datetime_local_input_step_seconds(granularity_minutes: int) -> int:
    """Step en segundos para ``input[type=datetime-local]``."""
    return granularity_minutes * 60
