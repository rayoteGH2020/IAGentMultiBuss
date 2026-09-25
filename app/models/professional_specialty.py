from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.professional import Professional
    from app.models.scheduling_service import SchedulingService


class ProfessionalSpecialty(Base, IdMixin, TimestampMixin):
    __tablename__ = "professional_specialties"
    __table_args__ = (
        UniqueConstraint(
            "professional_id",
            "service_id",
            name="uq_professional_specialties_prof_service",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    professional_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("professionals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    service_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("services.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    professional: Mapped[Professional] = relationship(
        "Professional",
        back_populates="specialties",
    )
    service: Mapped[SchedulingService] = relationship(
        "SchedulingService",
        back_populates="specialties",
    )
