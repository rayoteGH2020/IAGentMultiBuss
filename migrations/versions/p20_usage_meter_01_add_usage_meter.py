"""add usage_meter with RLS (Paso 20 H)

Revision ID: p20_usage_meter_01
Revises: p20_chat_citations_01
Create Date: 2026-05-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p20_usage_meter_01"
down_revision: str | None = "p20_chat_citations_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_meter",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("invoices_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rag_messages_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "analytics_queries_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "llm_cost_eur",
            sa.Numeric(12, 6),
            server_default="0",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "period"),
    )
    op.create_index(
        "ix_usage_meter_tenant_period",
        "usage_meter",
        ["tenant_id", "period"],
        unique=True,
    )

    op.execute(text("ALTER TABLE usage_meter ENABLE ROW LEVEL SECURITY;"))
    op.execute(text("ALTER TABLE usage_meter FORCE ROW LEVEL SECURITY;"))
    op.execute(
        text(
            """
            CREATE POLICY tenant_isolation ON usage_meter
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
        """
        )
    )
    op.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON usage_meter TO saas_app"))


def downgrade() -> None:
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON usage_meter;"))
    op.execute(text("ALTER TABLE usage_meter DISABLE ROW LEVEL SECURITY;"))
    op.drop_index("ix_usage_meter_tenant_period", table_name="usage_meter")
    op.drop_table("usage_meter")
