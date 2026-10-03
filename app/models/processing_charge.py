"""Cargos por procesado excepcional de documentos (repercutibles al cliente)."""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProcessingChargeStatus(enum.StrEnum):
    """Ciclo de vida del cargo, no del documento."""

    # Autorizado y pendiente de repercutir al cliente.
    pending = "pending"
    # Incluido en la liquidación mensual (proceso futuro).
    billed = "billed"
    # Condonado: se procesó pero no se cobra.
    waived = "waived"


class ProcessingCharge(Base):
    """Coste de un procesado que se salta los límites, imputable a un tenant.

    No es un documento fiscal ni genera factura: es el registro contable interno
    que permitirá, más adelante, un proceso mensual de repercusión. Por eso el
    coste de proveedor y el importe repercutible viven en columnas separadas.
    """

    __tablename__ = "processing_charges"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    document_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # Primer día del mes natural: clave de agrupación del futuro cierre mensual.
    period: Mapped[date] = mapped_column(Date, nullable=False)
    pages: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    # Estimación mostrada al superadmin en el momento de autorizar.
    estimated_cost_eur: Mapped[Decimal] = mapped_column(
        Numeric(12, 6),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )
    # Coste real del proveedor; NULL hasta que el worker termina.
    provider_cost_eur: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    # Importe a repercutir = coste de proveedor por el multiplicador configurado.
    billable_eur: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ProcessingChargeStatus.pending.value,
        server_default=text("'pending'"),
    )
    llm_call_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # SET NULL: si se borra el usuario que autorizó, el cargo sigue siendo válido.
    authorized_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Motivo de la autorización: por qué se saltó el límite y qué se esperaba.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_processing_charges_tenant_period", "tenant_id", "period"),
        Index("ix_processing_charges_document", "document_kind", "document_id"),
    )


__all__ = ["ProcessingCharge", "ProcessingChargeStatus"]
