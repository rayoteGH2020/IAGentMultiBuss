from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LLMCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    task: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)

    input_tokens: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    cost_eur: Mapped[Decimal] = mapped_column(Numeric(10, 6), server_default="0", nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="ok")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_llm_calls_tenant_created", "tenant_id", "created_at"),)
