"""Consumo de cupos mensuales por tenant, periodo y límite (D027)."""

from __future__ import annotations

from datetime import date, datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class QuotaUsage(Base):
    """Una fila por tenant, periodo (día 1 del mes natural) y código de límite.

    ``used`` es lo consumido en el periodo; ``extra`` es la ampliación del SADM
    válida solo para ese periodo. El tope efectivo es el del plan (con override)
    más ``extra``.
    """

    __tablename__ = "quota_usage"
    __table_args__ = (
        CheckConstraint("used >= 0", name="ck_quota_usage_used_non_negative"),
        CheckConstraint("extra >= 0", name="ck_quota_usage_extra_non_negative"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    period: Mapped[date] = mapped_column(Date, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    extra: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
