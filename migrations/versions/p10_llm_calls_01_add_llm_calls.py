"""add llm_calls with RLS

Revision ID: p10_llm_calls_01
Revises: p09_invoices_01
Create Date: 2026-05-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p10_llm_calls_01"
down_revision: str | None = "p09_invoices_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "cost_eur",
            sa.Numeric(10, 6),
            server_default="0",
            nullable=False,
        ),
        sa.Column("latency_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'ok'::character varying"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("langfuse_trace_id", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_llm_calls_tenant_id"), "llm_calls", ["tenant_id"], unique=False)
    op.create_index(
        "ix_llm_calls_tenant_created", "llm_calls", ["tenant_id", "created_at"], unique=False
    )

    tbl = "llm_calls"
    op.execute(text(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;"))
    op.execute(text(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY;"))
    op.execute(
        text(
            f"""
            CREATE POLICY tenant_isolation ON {tbl}
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
        """
        )
    )
    op.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON {tbl} TO saas_app"))


def downgrade() -> None:
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON llm_calls;"))
    op.execute(text("ALTER TABLE llm_calls DISABLE ROW LEVEL SECURITY;"))

    op.drop_index("ix_llm_calls_tenant_created", table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_tenant_id"), table_name="llm_calls")
    op.drop_table("llm_calls")
