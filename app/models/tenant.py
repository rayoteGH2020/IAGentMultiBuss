from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.membership import Membership


class Tenant(Base, IdMixin, TimestampMixin):
    __tablename__ = "tenants"

    clerk_org_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(
        String(32),
        default="basic",
        server_default=text("'basic'"),
        nullable=False,
    )
    # Codigo canonico del catalogo `plans` (Paso02). `plan` se mantiene por
    # compatibilidad una migracion mas; nuevos writes deben rellenar ambos.
    plan_code: Mapped[str] = mapped_column(
        String(32),
        default="basic",
        server_default=text("'basic'"),
        nullable=False,
        index=True,
    )
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    monthly_budget_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Billing Stripe (Paso09). IDs del proveedor; el plan interno sigue en plan_code.
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(128),
        unique=True,
        index=True,
        nullable=True,
    )
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String(128),
        index=True,
        nullable=True,
    )
    # none | active | past_due | canceled
    billing_status: Mapped[str] = mapped_column(
        String(32),
        default="none",
        server_default=text("'none'"),
        nullable=False,
    )

    memberships: Mapped[list["Membership"]] = relationship("Membership", back_populates="tenant")
