"""knowledge_chunks embedding vector(512) for voyage-3-lite

Revision ID: p18_knowledge_02
Revises: p18_knowledge_01
Create Date: 2026-05-27

voyage-3-lite devuelve embeddings de 512 dimensiones, no 1536.
Los chunks existentes deben reindexarse tras aplicar esta migración.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p18_knowledge_02"
down_revision: str | None = "p18_knowledge_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Sin USING: no se puede convertir 1536→512; vaciar chunks (reindexar documentos).
    op.execute(text("DELETE FROM knowledge_chunks"))
    op.execute(text("DROP INDEX IF EXISTS knowledge_chunks_embedding_hnsw_idx"))
    op.execute(
        text("ALTER TABLE knowledge_chunks ALTER COLUMN embedding TYPE vector(512)")
    )
    op.execute(
        text(
            "CREATE INDEX knowledge_chunks_embedding_hnsw_idx "
            "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
        )
    )


def downgrade() -> None:
    op.execute(text("DELETE FROM knowledge_chunks"))
    op.execute(text("DROP INDEX IF EXISTS knowledge_chunks_embedding_hnsw_idx"))
    op.execute(
        text("ALTER TABLE knowledge_chunks ALTER COLUMN embedding TYPE vector(1536)")
    )
    op.execute(
        text(
            "CREATE INDEX knowledge_chunks_embedding_hnsw_idx "
            "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
        )
    )
