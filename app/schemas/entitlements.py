"""DTOs de entitlements resueltos (Paso02)."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID  # noqa: TC003 — Pydantic requiere UUID en runtime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.entitlement_codes import FEATURE_CODES, LIMIT_CODES, is_known_feature, is_known_limit


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
        return value


class PlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    description: str | None = None
    sort_order: int
    is_active: bool
    is_public: bool
    stripe_price_id: str | None = None
