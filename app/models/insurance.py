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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.doc_type import DocType
    from app.models.llm_call import LLMCall


class InsuranceStatus(enum.StrEnum):
    pending = "pending"
    processing = "processing"
    ready = "ready"
    failed = "failed"
    reviewed = "reviewed"


class Insurance(Base):
    __tablename__ = "insurances"

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
    status: Mapped[InsuranceStatus] = mapped_column(
        Enum(InsuranceStatus, name="insurance_status", native_enum=True),
        nullable=False,
        default=InsuranceStatus.pending,
        server_default=text("'pending'::insurance_status"),
    )

    source_file_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_mime: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(300), nullable=True)

    aseguradora: Mapped[str | None] = mapped_column(String(300), nullable=True)
    numero_poliza: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tomador: Mapped[str | None] = mapped_column(String(300), nullable=True)
    cif_nif: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tipo_seguro: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fecha_inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
    fecha_fin: Mapped[date | None] = mapped_column(Date, nullable=True)
    prima: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="EUR",
        server_default=text("'EUR'"),
    )
    cobertura: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_extraction: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    llm_call_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    llm_call: Mapped[LLMCall | None] = relationship(
        "LLMCall",
        primaryjoin="Insurance.llm_call_id == foreign(LLMCall.id)",
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

    doc_type: Mapped[DocType] = relationship(back_populates="insurances")

    __table_args__ = (
        Index("ix_insurances_tenant_status", "tenant_id", "status"),
        Index("ix_insurances_tenant_fecha_inicio", "tenant_id", "fecha_inicio"),
        Index("ix_insurances_tenant_dismissed", "tenant_id", "dismissed_at"),
        Index(
            "ix_insurances_error_code",
            "error_code",
            postgresql_where=text("error_code IS NOT NULL"),
        ),
    )
