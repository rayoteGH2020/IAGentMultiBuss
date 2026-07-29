"""Channel integrations for external messaging (Paso 21 C)."""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.schemas.channel import ChannelIntegrationStatus


class ChannelType(enum.StrEnum):
    whatsapp = "whatsapp"
    telegram = "telegram"


class ChannelIntegration(Base):
    """External messaging channel integration per tenant (one row per channel max)."""

    __tablename__ = "channel_integrations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # ChannelType value stored as text
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    # WhatsApp only: phone_number_id from Meta (not the visible phone number)
    phone_number_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Human-readable label: "+34 612 345 678" (WA) or "@MyBot" (Telegram)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # WhatsApp access token / Telegram bot token — encrypted with ENCRYPTION_KEY (Fernet)
    api_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Telegram webhook secret sent to setWebhook — encrypted, returned in X-Telegram-Bot-Api-Secret-Token
    webhook_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ChannelIntegrationStatus.active.value,
        server_default=text("'active'"),
    )
    # Minimum RAG confidence to auto-reply; below this threshold → escalate to human
    confidence_threshold: Mapped[float] = mapped_column(
        Float(),
        nullable=False,
        default=0.5,
        server_default=text("0.5"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "channel", name="uq_channel_integration_per_tenant"),
        # Unicidad cross-tenant: evita enrutar webhooks WhatsApp al tenant equivocado
        Index(
            "uq_channel_integrations_wa_phone_active",
            "phone_number_id",
            unique=True,
            postgresql_where=text(
                "status = 'active' AND phone_number_id IS NOT NULL AND channel = 'whatsapp'"
            ),
        ),
    )


__all__ = [
    "ChannelIntegration",
    "ChannelIntegrationStatus",
    "ChannelType",
]
