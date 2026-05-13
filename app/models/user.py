from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.membership import Membership


class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"

    clerk_user_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))

    memberships: Mapped[list["Membership"]] = relationship("Membership", back_populates="user")
