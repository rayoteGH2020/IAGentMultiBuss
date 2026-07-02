"""Integración OAuth de calendario externo (Google Calendar, Paso 17)."""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.schemas.calendar import CalendarIntegrationStatus


class CalendarIntegrationProvider(enum.StrEnum):
    google = "google"


class CalendarIntegration(Base):
    """Tokens OAuth cifrados por usuario y tenant (una fila por proveedor)."""

    __tablename__ = "calendar_integrations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=CalendarIntegrationProvider.google.value,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=CalendarIntegrationStatus.active.value,
    )
    google_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_calendar_id: Mapped[str] = mapped_column(String(255), nullable=False, default="primary")
    access_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "provider",
            name="uq_calendar_integration_per_user",
        ),
        Index("ix_calendar_integrations_tenant_user", "tenant_id", "user_id"),
    )


__all__ = [
    "CalendarIntegration",
    "CalendarIntegrationProvider",
    "CalendarIntegrationStatus",
]
