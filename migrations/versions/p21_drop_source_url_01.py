"""drop source_url from knowledge_documents (ingesta URL eliminada del proyecto)

La feature de ingesta por URL (Paso 21 A) fue descartada. Esta migración elimina
la columna source_url que añadió p21_a_knowledge_url_faq_01. La columna faq_content
de la misma migración original se conserva (FAQ manual sigue activo).

Revision ID: p21_drop_source_url_01
Revises: p21_e_channel_response_cache_01
Create Date: 2026-05-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p21_drop_source_url_01"
down_revision: str | None = "p21_e_channel_response_cache_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("knowledge_documents", "source_url")


def downgrade() -> None:
    op.add_column(
        "knowledge_documents",
        sa.Column("source_url", sa.Text(), nullable=True),
    )
