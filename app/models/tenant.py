from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.membership import Membership


class Tenant(Base, IdMixin, TimestampMixin):
    __tablename__ = "tenants"

    clerk_org_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(String(32), default="free", nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    monthly_budget_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)

    memberships: Mapped[list["Membership"]] = relationship("Membership", back_populates="tenant")
