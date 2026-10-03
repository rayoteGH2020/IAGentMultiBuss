"""DTOs de entitlements resueltos (Paso02)."""

from __future__ import annotations

from datetime import date, datetime  # noqa: TC003 — Pydantic los requiere en runtime
from decimal import Decimal
from uuid import UUID  # noqa: TC003 — Pydantic requiere UUID en runtime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.entitlement_codes import (
    FEATURE_CODES,
    LIMIT_CODES,
    LIMIT_HISTORY_MONTHS,
    is_known_feature,
    is_known_limit,
)


class Entitlements(BaseModel):
    """Capacidades efectivas de un tenant tras catalogo + override + kill-switch."""

    model_config = ConfigDict(frozen=True)

    plan_code: str
    features: frozenset[str] = Field(default_factory=frozenset)
    limits: dict[str, Decimal | None] = Field(default_factory=dict)
    fail_closed: bool = False

    @field_validator("features", mode="before")
    @classmethod
    def _coerce_features(cls, value: object) -> frozenset[str]:
        if value is None:
            return frozenset()
        if isinstance(value, frozenset):
            return value
        if isinstance(value, (set, list, tuple)):
            return frozenset(str(item) for item in value)
        raise TypeError("features must be a set-like of strings")

    def has(self, feature: str) -> bool:
        """Feature desconocida o no incluida -> deny."""
        if not is_known_feature(feature):
            return False
        return feature in self.features

    def limit(self, code: str) -> Decimal | None:
        """Devuelve el tope; ``None`` solo si el catalogo/override declara unlimited.

        Codigo desconocido o ausente -> ``Decimal(0)`` (conservador / deny).
        """
        if not is_known_limit(code):
            return Decimal("0")
        if code not in self.limits:
            return Decimal("0")
        return self.limits[code]


class QuotaUsage(BaseModel):
    """Consumo actual de un límite: ``cap`` None = ilimitado."""

    used: int
    cap: int | None


class PlanLimitItem(BaseModel):
    """Límite del plan listo para mostrar ("Miembros del equipo", "15").

    ``used`` / ``percent`` solo si el consumo es medible (None en los límites
    por cliente final, como los mensajes por hora en canales).
    """

    label: str
    value: str
    used: int | None = None
    percent: int | None = None


class PlanSummary(BaseModel):
    """Resumen del plan efectivo del tenant para el cliente (catálogo + override)."""

    code: str
    name: str
    features: list[str]
    limits: list[PlanLimitItem]


class EntitlementsOverride(BaseModel):
    """Payload validado de ``tenants.settings['entitlements_override']``."""

    model_config = ConfigDict(extra="forbid")

    features: dict[str, bool] = Field(default_factory=dict)
    limits: dict[str, Decimal | None] = Field(default_factory=dict)

    @field_validator("features")
    @classmethod
    def _known_features(cls, value: dict[str, bool]) -> dict[str, bool]:
        unknown = sorted(code for code in value if code not in FEATURE_CODES)
        if unknown:
            raise ValueError(f"unknown feature codes in override: {', '.join(unknown)}")
        return value

    @field_validator("limits")
    @classmethod
    def _known_limits(cls, value: dict[str, Decimal | None]) -> dict[str, Decimal | None]:
        unknown = sorted(code for code in value if code not in LIMIT_CODES)
        if unknown:
            raise ValueError(f"unknown limit codes in override: {', '.join(unknown)}")
        # 0 o negativo ocultaría todas las facturas y tickets; sin límite = null.
        history = value.get(LIMIT_HISTORY_MONTHS)
        if LIMIT_HISTORY_MONTHS in value and history is not None and history < 1:
            raise ValueError("history_months must be at least 1 (null = no limit)")
        return value


class ScheduledPlanChange(BaseModel):
    """Cambio de plan pendiente en ``tenants.settings['scheduled_plan_change']`` (D027).

    Los cambios de plan tras la primera asignación se aplican el día 1 del mes
    siguiente (``effective_date``).
    """

    model_config = ConfigDict(extra="forbid")

    plan_code: str
    effective_date: date
    requested_by: UUID | None = None
    requested_at: datetime
    reason: str | None = None


class PlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    description: str | None = None
    sort_order: int
    is_active: bool
    is_public: bool
