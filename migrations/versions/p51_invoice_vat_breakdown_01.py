"""add vat_breakdown JSONB to invoices

Revision ID: p51_invoice_vat_breakdown_01
Revises: p50_admin_fields_01
Create Date: 2026-07-02

Desglose multi-IVA por factura (CorreccionesCDX punto 5).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "p51_invoice_vat_breakdown_01"
down_revision: str | None = "p50_admin_fields_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("vat_breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("invoices", "vat_breakdown")
