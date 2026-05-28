"""Contadores mensuales de uso por tenant (billing / límites)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UsageMeter(Base):
    """Uso agregado por tenant y periodo (primer día del mes calendario)."""

    __tablename__ = "usage_meter"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    period: Mapped[date] = mapped_column(Date, primary_key=True)
    invoices_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rag_messages_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    analytics_queries_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_cost_eur: Mapped[Decimal] = mapped_column(
        Numeric(12, 6),
        nullable=False,
        default=Decimal("0"),
    )
