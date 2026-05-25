"""add chat_threads and chat_messages with RLS; pg_trgm and unaccent

Revision ID: p16_chat_01
Revises: p11_doc_types_tickets_01
Create Date: 2026-05-25

Módulo 1.5 — consulta documental: hilos y mensajes con tool-calling.
Extensiones para búsqueda tolerante en proveedor/comercio (Paso 16 Fase B).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p16_chat_01"
down_revision: str | None = "p11_doc_types_tickets_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

chat_message_role = postgresql.ENUM(
    "user",
    "assistant",
    "tool",
    name="chat_message_role",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    op.execute(text('CREATE EXTENSION IF NOT EXISTS "unaccent";'))
    op.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm;"))

    chat_message_role.create(bind, checkfirst=True)

    op.create_table(
        "chat_threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_chat_threads_tenant_id"), "chat_threads", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_chat_threads_user_id"), "chat_threads", ["user_id"], unique=False
    )
    op.create_index(
        "ix_chat_threads_tenant_user_updated",
        "chat_threads",
        ["tenant_id", "user_id", "updated_at"],
        unique=False,
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "role",
            chat_message_role,
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_call", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tool_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("llm_call_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["chat_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_chat_messages_thread_id"), "chat_messages", ["thread_id"], unique=False
    )
    op.create_index(
        op.f("ix_chat_messages_tenant_id"), "chat_messages", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_chat_messages_tenant_thread_created",
        "chat_messages",
        ["tenant_id", "thread_id", "created_at"],
        unique=False,
    )

    for tbl in ("chat_threads", "chat_messages"):
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
        op.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON {tbl} TO saas_app")
        )


def downgrade() -> None:
    for tbl in ("chat_messages", "chat_threads"):
        op.execute(text(f"DROP POLICY IF EXISTS tenant_isolation ON {tbl};"))
        op.execute(text(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY;"))

    op.drop_index("ix_chat_messages_tenant_thread_created", table_name="chat_messages")
    op.drop_index(op.f("ix_chat_messages_tenant_id"), table_name="chat_messages")
    op.drop_index(op.f("ix_chat_messages_thread_id"), table_name="chat_messages")
    op.drop_table("chat_messages")

    op.drop_index("ix_chat_threads_tenant_user_updated", table_name="chat_threads")
    op.drop_index(op.f("ix_chat_threads_user_id"), table_name="chat_threads")
    op.drop_index(op.f("ix_chat_threads_tenant_id"), table_name="chat_threads")
    op.drop_table("chat_threads")

    chat_message_role.drop(op.get_bind(), checkfirst=True)
