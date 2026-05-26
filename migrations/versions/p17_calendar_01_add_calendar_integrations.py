"""add calendar_integrations with RLS

Revision ID: p17_calendar_01
Revises: p17_llm_calls_01
Create Date: 2026-05-26

Paso 17 Fase C — tokens OAuth Google Calendar cifrados por usuario/tenant.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p17_calendar_01"
down_revision: str | None = "p17_llm_calls_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calendar_integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="google"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("google_email", sa.String(length=255), nullable=True),
        sa.Column(
            "google_calendar_id",
            sa.String(length=255),
            nullable=False,
            server_default="primary",
        ),
        sa.Column("access_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("refresh_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "provider",
            name="uq_calendar_integration_per_user",
        ),
    )
    op.create_index(
        op.f("ix_calendar_integrations_tenant_id"),
        "calendar_integrations",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_calendar_integrations_user_id"),
        "calendar_integrations",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_calendar_integrations_tenant_user",
        "calendar_integrations",
        ["tenant_id", "user_id"],
        unique=False,
    )

    op.execute(text("ALTER TABLE calendar_integrations ENABLE ROW LEVEL SECURITY;"))
    op.execute(text("ALTER TABLE calendar_integrations FORCE ROW LEVEL SECURITY;"))
    op.execute(
        text(
            """
            CREATE POLICY tenant_isolation ON calendar_integrations
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
        """
        )
    )
    op.execute(
        text(
            "GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE "
            "ON calendar_integrations TO saas_app"
        )
    )


def downgrade() -> None:
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON calendar_integrations;"))
    op.execute(text("ALTER TABLE calendar_integrations DISABLE ROW LEVEL SECURITY;"))
    op.drop_index(
        "ix_calendar_integrations_tenant_user",
        table_name="calendar_integrations",
    )
    op.drop_index(
        op.f("ix_calendar_integrations_user_id"),
        table_name="calendar_integrations",
    )
    op.drop_index(
        op.f("ix_calendar_integrations_tenant_id"),
        table_name="calendar_integrations",
    )
    op.drop_table("calendar_integrations")
