from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.professional_specialty import ProfessionalSpecialty
    from app.models.professional_working_hour import ProfessionalWorkingHour
    from app.models.user import User


class Professional(Base, IdMixin, TimestampMixin):
    __tablename__ = "professionals"
    __table_args__ = (
        Index(
            "ix_professionals_tenant_active_bookable_sort",
            "tenant_id",
            "is_active",
            "is_bookable",
            "sort_order",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    color: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="#6366f1",
        server_default=text("'#6366f1'"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    is_bookable: Mapped[bool] = mapped_column(
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

    user: Mapped[User | None] = relationship("User")
    working_hours: Mapped[list[ProfessionalWorkingHour]] = relationship(
        "ProfessionalWorkingHour",
        back_populates="professional",
        cascade="all, delete-orphan",
    )
    specialties: Mapped[list[ProfessionalSpecialty]] = relationship(
        "ProfessionalSpecialty",
        back_populates="professional",
        cascade="all, delete-orphan",
    )
