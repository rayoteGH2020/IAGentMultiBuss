from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.invoice import Invoice
    from app.models.ticket import Ticket


class DocTypeCode(enum.StrEnum):
    factura = "factura"
    ticket = "ticket"


class DocType(Base):
    """Catálogo global de tipos de documento administrativo.

    Tabla de referencia sin tenant_id: los valores se siembran por migración
    y son extensibles añadiendo filas (p. ej. albarán, contrato).
    """

    __tablename__ = "doc_types"
    __table_args__ = (UniqueConstraint("code", name="doc_types_code_key"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    invoices: Mapped[list[Invoice]] = relationship(back_populates="doc_type")
    tickets: Mapped[list[Ticket]] = relationship(back_populates="doc_type")
