"""Elimina el índice único duplicado de doc_types.code.

La constraint `doc_types_code_key` ya garantiza unicidad. El índice
`ix_doc_types_code` quedó como artefacto de un `unique=True, index=True`
en el modelo ORM y es redundante.

Revision ID: p58_drop_doc_types_dup_idx
Revises: p57_schema_defaults_idx_01
Create Date: 2026-07-26
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p58_drop_doc_types_dup_idx"
down_revision: str | None = "p57_schema_defaults_idx_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS ix_doc_types_code;"))


def downgrade() -> None:
    op.create_index("ix_doc_types_code", "doc_types", ["code"], unique=True)
