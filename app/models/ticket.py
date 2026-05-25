from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.doc_type import DocType
    from app.models.llm_call import LLMCall


class TicketStatus(enum.StrEnum):
    pending = "pending"
    processing = "processing"
    ready = "ready"
    failed = "failed"
    reviewed = "reviewed"


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    doc_type_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("doc_types.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus, name="ticket_status", native_enum=True),
        nullable=False,
        default=TicketStatus.pending,
    )

    source_file_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_mime: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(300), nullable=True)

    fecha: Mapped[date | None] = mapped_column(Date, nullable=True)
    comercio: Mapped[str | None] = mapped_column(String(300), nullable=True)
    numero_ticket: Mapped[str | None] = mapped_column(String(100), nullable=True)
    forma_pago: Mapped[str | None] = mapped_column(String(100), nullable=True)
    base_imponible: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    iva_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    iva_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")

    raw_extraction: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_call_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    llm_call: Mapped[LLMCall | None] = relationship(
        "LLMCall",
        primaryjoin="Ticket.llm_call_id == foreign(LLMCall.id)",
        viewonly=True,
        uselist=False,
    )

    reviewed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    doc_type: Mapped[DocType] = relationship(back_populates="tickets")

    __table_args__ = (
        Index("ix_tickets_tenant_status", "tenant_id", "status"),
        Index("ix_tickets_tenant_fecha", "tenant_id", "fecha"),
    )
