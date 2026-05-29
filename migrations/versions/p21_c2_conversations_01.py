"""add conversations and channel_messages tables with RLS (Paso 21 C2)

Nota: la tabla se llama channel_messages (no messages) para evitar colisión
con el modelo messages del módulo RAG definido en arquitectura.md §5.

Revision ID: p21_c2_conversations_01
Revises: p21_c_channel_integrations_01
Create Date: 2026-05-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p21_c2_conversations_01"
down_revision: str | None = "p21_c_channel_integrations_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- conversations ---
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("customer_identifier", sa.String(255), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_conversations_tenant_id"), "conversations", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_conversations_tenant_channel_customer",
        "conversations",
        ["tenant_id", "channel", "customer_identifier"],
        unique=False,
    )
    op.execute(text("ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;"))
    op.execute(text("ALTER TABLE conversations FORCE ROW LEVEL SECURITY;"))
    op.execute(
        text("""
            CREATE POLICY tenant_isolation ON conversations
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
        """)
    )
    op.execute(
        text("GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON conversations TO saas_app")
    )

    # --- channel_messages ---
    op.create_table(
        "channel_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("llm_call_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_channel_messages_tenant_id"), "channel_messages", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_channel_messages_conversation_created",
        "channel_messages",
        ["conversation_id", "created_at"],
        unique=False,
    )
    op.execute(text("ALTER TABLE channel_messages ENABLE ROW LEVEL SECURITY;"))
    op.execute(text("ALTER TABLE channel_messages FORCE ROW LEVEL SECURITY;"))
    op.execute(
        text("""
            CREATE POLICY tenant_isolation ON channel_messages
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
        """)
    )
    op.execute(
        text(
            "GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON channel_messages TO saas_app"
        )
    )


def downgrade() -> None:
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON channel_messages;"))
    op.execute(text("ALTER TABLE channel_messages DISABLE ROW LEVEL SECURITY;"))
    op.drop_index(
        "ix_channel_messages_conversation_created", table_name="channel_messages"
    )
    op.drop_index(op.f("ix_channel_messages_tenant_id"), table_name="channel_messages")
    op.drop_table("channel_messages")

    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON conversations;"))
    op.execute(text("ALTER TABLE conversations DISABLE ROW LEVEL SECURITY;"))
    op.drop_index("ix_conversations_tenant_channel_customer", table_name="conversations")
    op.drop_index(op.f("ix_conversations_tenant_id"), table_name="conversations")
    op.drop_table("conversations")
