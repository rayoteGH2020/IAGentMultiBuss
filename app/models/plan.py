"""Catalogo global de planes y entitlements (sin tenant_id / sin RLS)."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Plan(Base, TimestampMixin):
    """Plan comercial global (catalogo)."""

    __tablename__ = "plans"
    __table_args__ = (UniqueConstraint("code", name="plans_code_key"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    is_public: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    stripe_price_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    entitlements: Mapped[list[PlanEntitlement]] = relationship(
        "PlanEntitlement",
        back_populates="plan",
        cascade="all, delete-orphan",
    )


class PlanEntitlement(Base, TimestampMixin):
    """Fila de feature o limite asociada a un plan."""

    __tablename__ = "plan_entitlements"
    __table_args__ = (
        UniqueConstraint("plan_id", "kind", "code", name="plan_entitlements_plan_kind_code_key"),
        CheckConstraint(
            "kind IN ('feature', 'limit')",
            name="plan_entitlements_kind_check",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    limit_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)

    plan: Mapped[Plan] = relationship("Plan", back_populates="entitlements")
