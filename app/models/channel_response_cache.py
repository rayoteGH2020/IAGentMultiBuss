"""ORM model for semantic response cache — canales externos (Paso 21)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChannelResponseCache(Base):
    """Caché semántico de respuestas para canales externos.

    Almacena pares (pregunta_embedding, respuesta) por tenant para evitar llamadas
    LLM repetidas cuando un cliente formula preguntas semánticamente similares.
    El canal se guarda solo para auditoría; la búsqueda es cross-canal por tenant.
    """

    __tablename__ = "channel_response_cache"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Solo auditoría: la búsqueda no filtra por canal (una respuesta WA sirve para TG del mismo tenant).
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    question_embedding: Mapped[list[float]] = mapped_column(Vector(512), nullable=False)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    hit_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    __table_args__ = (
        Index("ix_channel_response_cache_tenant_created", "tenant_id", "created_at"),
        Index(
            "ix_channel_response_cache_embedding",
            "question_embedding",
            postgresql_using="hnsw",
            postgresql_ops={"question_embedding": "vector_cosine_ops"},
        ),
    )
