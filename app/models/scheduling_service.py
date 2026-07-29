from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.professional_specialty import ProfessionalSpecialty


class SchedulingService(Base, IdMixin, TimestampMixin):
    """Catálogo de servicios del centro. Tabla `services` (decisión 12)."""

    __tablename__ = "services"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_services_tenant_slug"),
        CheckConstraint(
            "duration_minutes >= 15 AND duration_minutes <= 240",
            name="ck_services_duration_minutes",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    specialties: Mapped[list[ProfessionalSpecialty]] = relationship(
        "ProfessionalSpecialty",
        back_populates="service",
    )
