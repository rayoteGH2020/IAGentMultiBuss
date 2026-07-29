"""add persistent Clerk membership revocation state

Revision ID: p55_membership_active_01
Revises: p54_doc_proc_attempts_rls_01
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p55_membership_active_01"
down_revision: str | None = "p54_doc_proc_attempts_rls_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memberships",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("memberships", "is_active")
