from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.scheduling_defaults import DEFAULT_MEMBERSHIP_PERMISSIONS
from app.models.base import Base, IdMixin, TimestampMixin
from app.models.tenant import Tenant
from app.models.user import User

_MEMBERSHIP_PERMISSIONS_SERVER_DEFAULT = text(
    """'{"appointments": {"view": true, "create": false, "edit": false, "cancel": false}}'::jsonb"""
)


class Membership(Base, IdMixin, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant"),)

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(
        String(32),
        default="member",
        server_default=text("'member'"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )
    permissions: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: dict(DEFAULT_MEMBERSHIP_PERMISSIONS),
        server_default=_MEMBERSHIP_PERMISSIONS_SERVER_DEFAULT,
    )
    # Baja solicitada al SADM y aún no ejecutada en Clerk. Al ejecutarla, el
    # webhook desactiva la fila; si se reactiva, estos campos se limpian.
    removal_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    removal_effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    @property
    def removal_pending(self) -> bool:
        return self.removal_effective_date is not None

    def clear_removal_request(self) -> None:
        self.removal_requested_at = None
        self.removal_effective_date = None

    user: Mapped[User] = relationship(back_populates="memberships")
    tenant: Mapped[Tenant] = relationship(back_populates="memberships")
