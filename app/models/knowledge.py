"""Modelos ORM del módulo 2 – base de conocimiento RAG (Paso 18)."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class KnowledgeDocumentStatus(enum.StrEnum):
    pending = "pending"  # subido, job encolado, sin procesar aún
    indexing = "indexing"  # worker extrayendo, troceando y embebiendo
    ready = "ready"  # indexación completada; chunks disponibles para búsqueda
    failed = "failed"  # error; error_message contiene el detalle visible en UI


class KnowledgeDocumentKind(enum.StrEnum):
    contract = "contract"  # Contrato (marco, SLA…)
    terms = "terms"  # Condiciones generales, privacidad
    schedule = "schedule"  # Horarios comerciales, turnos
    services = "services"  # Catálogo de servicios, tarifas
    policy = "policy"  # Políticas internas de la empresa
    faq = "faq"  # Preguntas frecuentes
    manual = "manual"  # Manuales y procedimientos
    other = "other"  # Resto de documentos


class KnowledgeDocument(Base):
    """Documento fuente del RAG: PDF, TXT o MD subido por el tenant."""

    __tablename__ = "knowledge_documents"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[KnowledgeDocumentKind] = mapped_column(
        Enum(KnowledgeDocumentKind, name="knowledge_document_kind", native_enum=True),
        nullable=False,
    )
    # name: título editable; por defecto se rellena con el nombre del fichero original.
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    source_file_key: Mapped[str] = mapped_column(String(500), nullable=False)
    source_mime: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[KnowledgeDocumentStatus] = mapped_column(
        Enum(KnowledgeDocumentStatus, name="knowledge_document_status", native_enum=True),
        nullable=False,
        default=KnowledgeDocumentStatus.pending,
    )
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        # SET NULL: si el usuario es borrado, el documento y su historial se conservan.
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # FAQ manual (Paso 21 B): pares Q/A serializados; null para documentos no-FAQ.
    faq_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    uploader: Mapped[User | None] = relationship(
        "User",
        primaryjoin="KnowledgeDocument.uploaded_by == foreign(User.id)",
        viewonly=True,
        uselist=False,
    )
    chunks: Mapped[list[KnowledgeChunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="KnowledgeChunk.position",
    )

    __table_args__ = (
        Index("ix_knowledge_documents_tenant_status", "tenant_id", "status"),
        Index("ix_knowledge_documents_tenant_kind", "tenant_id", "kind"),
    )


class KnowledgeChunk(Base):
    """Fragmento de texto con embedding para búsqueda vectorial e híbrida."""

    __tablename__ = "knowledge_chunks"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        # CASCADE: borrar el documento elimina todos sus chunks en BD.
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # context: línea de contexto generada por contextual retrieval (sub-fase opcional).
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    # embedding: vector coseno 512d — voyage-3-lite (única dimensión soportada por el modelo).
    # La app NUNCA lee este campo directamente; lo usa Postgres para búsqueda vectorial.
    embedding: Mapped[list[float]] = mapped_column(Vector(512), nullable=False)
    # search_vector: columna generada por Postgres; la app nunca escribe aquí.
    # Usada en búsqueda BM25/full-text en Paso 19 vía índice GIN.
    # 'spanish': configuración con stop-words en castellano, adecuada para contenido de pymes españolas.
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('spanish', content)", persisted=True),
        nullable=True,
    )
    # Renombrado a chunk_metadata en Python para no colisionar con Base.metadata de SQLAlchemy.
    chunk_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, nullable=True, default=dict
    )
    # position preserva el orden de los chunks dentro del documento original.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")

    __table_args__ = (
        Index(
            "ix_knowledge_chunks_tenant_doc_position",
            "tenant_id",
            "document_id",
            "position",
        ),
    )
