"""add monthly_budget_eur to tenants (Paso 50)

Añade el campo monthly_budget_eur a tenants, presente en arquitectura.md §5
pero ausente del modelo ORM original.

Revision ID: p50_admin_fields_01
Revises: p21_drop_source_url_01
Create Date: 2026-05-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p50_admin_fields_01"
down_revision: str | None = "p21_drop_source_url_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("monthly_budget_eur", sa.Numeric(10, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "monthly_budget_eur")
