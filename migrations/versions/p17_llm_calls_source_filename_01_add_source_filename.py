"""add source_filename to llm_calls

Revision ID: p17_llm_calls_01
Revises: p16_audit_01
Create Date: 2026-05-25

Nombre original del fichero asociado a la llamada (extracción/clasificación).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p17_llm_calls_01"
down_revision: str | None = "p16_audit_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_calls",
        sa.Column("source_filename", sa.String(length=300), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("llm_calls", "source_filename")
