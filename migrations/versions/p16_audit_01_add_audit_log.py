"""add audit_log with RLS

Revision ID: p16_audit_01
Revises: p16_chat_01
Create Date: 2026-05-25

Paso 16 Fase G — auditoría de mensajes de chat y tools ejecutadas.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p16_audit_01"
down_revision: str | None = "p16_chat_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_log_tenant_id"), "audit_log", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_audit_log_user_id"), "audit_log", ["user_id"], unique=False)
    op.create_index("ix_audit_log_tenant_created", "audit_log", ["tenant_id", "created_at"])
    op.create_index("ix_audit_log_tenant_action", "audit_log", ["tenant_id", "action"])

    op.execute(text("ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;"))
    op.execute(text("ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;"))
    op.execute(
        text(
            """
            CREATE POLICY tenant_isolation ON audit_log
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
        """
        )
    )
    op.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON audit_log TO saas_app"))


def downgrade() -> None:
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON audit_log;"))
    op.execute(text("ALTER TABLE audit_log DISABLE ROW LEVEL SECURITY;"))
    op.drop_index("ix_audit_log_tenant_action", table_name="audit_log")
    op.drop_index("ix_audit_log_tenant_created", table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_user_id"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_tenant_id"), table_name="audit_log")
    op.drop_table("audit_log")
