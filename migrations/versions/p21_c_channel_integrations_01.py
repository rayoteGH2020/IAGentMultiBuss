"""add channel_integrations table with RLS (Paso 21 C)

Tabla central que asocia un tenant con sus canales externos (WhatsApp, Telegram).
Incluye dos políticas RLS:
  - tenant_isolation: acceso normal filtrado por app.current_tenant.
  - webhook_select: permite SELECT cross-tenant cuando app.webhook_lookup='true'
    (flag local a la transacción, usado en webhook handlers para lookup inicial).

Revision ID: p21_c_channel_integrations_01
Revises: p21_a_knowledge_url_faq_01
Create Date: 2026-05-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p21_c_channel_integrations_01"
down_revision: str | None = "p21_a_knowledge_url_faq_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "channel_integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("phone_number_id", sa.String(64), nullable=True),
        sa.Column("display_name", sa.String(128), nullable=True),
        sa.Column("api_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("webhook_secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("confidence_threshold", sa.Float(), nullable=False, server_default="0.5"),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "channel", name="uq_channel_integration_per_tenant"),
    )
    op.create_index(
        op.f("ix_channel_integrations_tenant_id"),
        "channel_integrations",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_channel_integrations_phone_number_id",
        "channel_integrations",
        ["phone_number_id"],
        unique=False,
    )

    op.execute(text("ALTER TABLE channel_integrations ENABLE ROW LEVEL SECURITY;"))
    op.execute(text("ALTER TABLE channel_integrations FORCE ROW LEVEL SECURITY;"))
    # Política estándar: acceso filtrado por tenant activo en la sesión
    op.execute(
        text("""
            CREATE POLICY tenant_isolation ON channel_integrations
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
        """)
    )
    # Política permisiva para webhook handlers: permite SELECT cross-tenant
    # cuando app.webhook_lookup='true' (set_config con is_local=true → revierte al commit).
    # Necesaria porque saas_app tiene NOBYPASSRLS y los webhooks no conocen el tenant de antemano.
    op.execute(
        text("""
            CREATE POLICY webhook_select ON channel_integrations
            AS PERMISSIVE
            FOR SELECT
            USING (current_setting('app.webhook_lookup', true) = 'true');
        """)
    )
    op.execute(
        text(
            "GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE"
            " ON channel_integrations TO saas_app"
        )
    )


def downgrade() -> None:
    op.execute(text("DROP POLICY IF EXISTS webhook_select ON channel_integrations;"))
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON channel_integrations;"))
    op.execute(text("ALTER TABLE channel_integrations DISABLE ROW LEVEL SECURITY;"))
    op.drop_index(
        "ix_channel_integrations_phone_number_id", table_name="channel_integrations"
    )
    op.drop_index(
        op.f("ix_channel_integrations_tenant_id"), table_name="channel_integrations"
    )
    op.drop_table("channel_integrations")
