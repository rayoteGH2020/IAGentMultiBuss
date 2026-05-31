"""create channel_response_cache table with HNSW index and RLS (Paso 21)

Caché semántico para canales externos: almacena embeddings de preguntas y
respuestas generadas para evitar llamadas LLM repetidas ante preguntas similares.

La columna question_embedding (vector(512)) se añade con ALTER TABLE porque
Alembic no conoce el tipo pgvector de forma nativa (mismo patrón que p18_knowledge_01).

Revision ID: p21_e_channel_response_cache_01
Revises: p21_c2_conversations_01
Create Date: 2026-05-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p21_e_channel_response_cache_01"
down_revision: str | None = "p21_c2_conversations_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "channel_response_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # question_embedding: vector(512) vía ALTER TABLE (pgvector no lo soporta Alembic nativamente).
    # La tabla está vacía en este punto, por lo que NOT NULL sin DEFAULT es válido en PG 16.
    op.execute(
        text(
            "ALTER TABLE channel_response_cache"
            " ADD COLUMN question_embedding vector(512) NOT NULL;"
        )
    )

    op.create_index(
        op.f("ix_channel_response_cache_tenant_id"),
        "channel_response_cache",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_channel_response_cache_tenant_created",
        "channel_response_cache",
        ["tenant_id", "created_at"],
        unique=False,
    )

    # Índice HNSW para búsqueda vectorial por coseno (same ops as knowledge_chunks).
    op.execute(
        text(
            "CREATE INDEX ix_channel_response_cache_embedding"
            " ON channel_response_cache"
            " USING hnsw (question_embedding vector_cosine_ops);"
        )
    )

    op.execute(text("ALTER TABLE channel_response_cache ENABLE ROW LEVEL SECURITY;"))
    op.execute(text("ALTER TABLE channel_response_cache FORCE ROW LEVEL SECURITY;"))
    op.execute(
        text("""
            CREATE POLICY tenant_isolation ON channel_response_cache
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
        """)
    )
    op.execute(
        text(
            "GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE"
            " ON channel_response_cache TO saas_app;"
        )
    )


def downgrade() -> None:
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON channel_response_cache;"))
    op.execute(text("ALTER TABLE channel_response_cache DISABLE ROW LEVEL SECURITY;"))
    op.drop_index(
        "ix_channel_response_cache_embedding", table_name="channel_response_cache"
    )
    op.drop_index(
        "ix_channel_response_cache_tenant_created", table_name="channel_response_cache"
    )
    op.drop_index(
        op.f("ix_channel_response_cache_tenant_id"), table_name="channel_response_cache"
    )
    op.drop_table("channel_response_cache")
