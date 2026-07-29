"""Historial de intentos de extracción por documento (factura/ticket)."""

from __future__ import annotations

import enum
from datetime import datetime  # noqa: TC003 — Mapped[datetime] requiere tipo en runtime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DocumentKind(enum.StrEnum):
    invoice = "invoice"
    ticket = "ticket"


class ProcessingAttemptStatus(enum.StrEnum):
    processing = "processing"
    ok = "ok"
    failed = "failed"


class DocumentProcessingAttempt(Base):
    """Un intento de extracción LLM sobre un invoice o ticket."""

    __tablename__ = "document_processing_attempts"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    document_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    llm_call_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # Valores de app.core.document_processing_errors.DocumentErrorCode. Se guarda
    # como texto (no ENUM nativo) porque la taxonomía de errores crecerá con cada
    # límite nuevo y no compensa un ALTER TYPE por cada uno.
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "document_kind",
            "document_id",
            "attempt_number",
            name="uq_document_processing_attempt",
        ),
        Index("ix_doc_proc_attempts_tenant_doc", "tenant_id", "document_kind", "document_id"),
    )


__all__ = [
    "DocumentKind",
    "DocumentProcessingAttempt",
    "ProcessingAttemptStatus",
]
