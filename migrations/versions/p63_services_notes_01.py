"""Add optional notes (observaciones) to services catalog.

Revision ID: p63_services_notes_01
Revises: p62_contracts_insurances_01
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p63_services_notes_01"
down_revision: str | None = "p62_contracts_insurances_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "services",
        sa.Column("notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("services", "notes")
