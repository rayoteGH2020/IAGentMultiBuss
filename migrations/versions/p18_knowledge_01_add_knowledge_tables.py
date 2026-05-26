"""add knowledge_documents and knowledge_chunks with pgvector and RLS

Revision ID: p18_knowledge_01
Revises: p17_calendar_01
Create Date: 2026-05-26

Paso 18 Fase C — pipeline de ingesta RAG: tablas de documentos y chunks
con embedding vectorial (pgvector), búsqueda full-text (tsvector generado),
índices HNSW y GIN, y aislamiento multi-tenant por RLS.

Decisión: tsvector con configuración 'spanish' (stop-words en castellano)
alineada con el dominio del producto (pymes españolas).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p18_knowledge_01"
down_revision: str | None = "p17_calendar_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

knowledge_document_kind = postgresql.ENUM(
    "contract",
    "terms",
    "schedule",
    "services",
    "policy",
    "faq",
    "manual",
    "other",
    name="knowledge_document_kind",
    create_type=False,
)

knowledge_document_status = postgresql.ENUM(
    "pending",
    "indexing",
    "ready",
    "failed",
    name="knowledge_document_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    knowledge_document_kind.create(bind, checkfirst=True)
    knowledge_document_status.create(bind, checkfirst=True)

    # ── knowledge_documents ───────────────────────────────────────────────────
    op.create_table(
        "knowledge_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", knowledge_document_kind, nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("original_filename", sa.String(300), nullable=False),
        sa.Column("source_file_key", sa.String(500), nullable=False),
        sa.Column("source_mime", sa.String(100), nullable=False),
        sa.Column(
            "status",
            knowledge_document_status,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_knowledge_documents_tenant_id"),
        "knowledge_documents",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_documents_tenant_status",
        "knowledge_documents",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_documents_tenant_kind",
        "knowledge_documents",
        ["tenant_id", "kind"],
        unique=False,
    )

    # ── knowledge_chunks ──────────────────────────────────────────────────────
    # Las columnas embedding (vector) y search_vector (tsvector generado) se añaden
    # con ALTER TABLE porque Alembic no conoce estos tipos de forma nativa.
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default="{}",
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_knowledge_chunks_tenant_id"),
        "knowledge_chunks",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_chunks_document_id"),
        "knowledge_chunks",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_chunks_tenant_doc_position",
        "knowledge_chunks",
        ["tenant_id", "document_id", "position"],
        unique=False,
    )

    # Columna embedding: tipo vector(1536) de pgvector.
    # La tabla está vacía en este punto, por lo que NOT NULL sin DEFAULT es válido en PG 16.
    op.execute(
        text("ALTER TABLE knowledge_chunks ADD COLUMN embedding vector(1536) NOT NULL;")
    )

    # Columna search_vector: generada siempre por Postgres a partir de content.
    # La app nunca escribe aquí; se usa solo en búsqueda BM25 en Paso 19.
    op.execute(
        text(
            "ALTER TABLE knowledge_chunks ADD COLUMN search_vector tsvector "
            "GENERATED ALWAYS AS (to_tsvector('spanish', content)) STORED;"
        )
    )

    # Índice HNSW para búsqueda vectorial por coseno (módulo 2 Paso 19).
    op.execute(
        text(
            "CREATE INDEX knowledge_chunks_embedding_hnsw_idx "
            "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);"
        )
    )
    # Índice GIN para búsqueda full-text / BM25 (módulo 2 Paso 19).
    op.execute(
        text(
            "CREATE INDEX knowledge_chunks_search_vector_gin_idx "
            "ON knowledge_chunks USING gin (search_vector);"
        )
    )

    # ── RLS + GRANTs ─────────────────────────────────────────────────────────
    for tbl in ("knowledge_documents", "knowledge_chunks"):
        op.execute(text(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;"))
        op.execute(text(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY;"))
        op.execute(
            text(
                f"""
                CREATE POLICY tenant_isolation ON {tbl}
                USING (tenant_id::text = current_setting('app.current_tenant', true))
                WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
                """
            )
        )
        op.execute(
            text(
                f"GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON {tbl} TO saas_app;"
            )
        )


def downgrade() -> None:
    for tbl in ("knowledge_chunks", "knowledge_documents"):
        op.execute(text(f"DROP POLICY IF EXISTS tenant_isolation ON {tbl};"))
        op.execute(text(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY;"))

    # Los índices HNSW y GIN se eliminan con la tabla; los normales se borran explícitamente.
    op.drop_index(
        "ix_knowledge_chunks_tenant_doc_position", table_name="knowledge_chunks"
    )
    op.drop_index(
        op.f("ix_knowledge_chunks_document_id"), table_name="knowledge_chunks"
    )
    op.drop_index(op.f("ix_knowledge_chunks_tenant_id"), table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")

    op.drop_index(
        "ix_knowledge_documents_tenant_kind", table_name="knowledge_documents"
    )
    op.drop_index(
        "ix_knowledge_documents_tenant_status", table_name="knowledge_documents"
    )
    op.drop_index(
        op.f("ix_knowledge_documents_tenant_id"), table_name="knowledge_documents"
    )
    op.drop_table("knowledge_documents")

    knowledge_document_status.drop(op.get_bind(), checkfirst=True)
    knowledge_document_kind.drop(op.get_bind(), checkfirst=True)
