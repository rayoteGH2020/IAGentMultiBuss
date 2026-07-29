from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint, text
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

    user: Mapped[User] = relationship(back_populates="memberships")
    tenant: Mapped[Tenant] = relationship(back_populates="memberships")
