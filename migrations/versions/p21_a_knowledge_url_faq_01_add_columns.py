"""add source_url and faq_content to knowledge_documents (Paso 21 A/B)

Ambas columnas son nullable sin valor por defecto: las filas existentes reciben
NULL automáticamente sin bloqueo de tabla ni backfill.

Revision ID: p21_a_knowledge_url_faq_01
Revises: p20_usage_meter_01
Create Date: 2026-05-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p21_a_knowledge_url_faq_01"
down_revision: str | None = "p20_usage_meter_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_documents",
        sa.Column("source_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("faq_content", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_documents", "faq_content")
    op.drop_column("knowledge_documents", "source_url")
