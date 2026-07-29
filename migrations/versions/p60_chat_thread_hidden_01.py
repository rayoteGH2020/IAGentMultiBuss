"""Soft-hide chat threads (UI only; rows retained).

Revision ID: p60_chat_thread_hidden_01
Revises: p59_wa_phone_unique_01
Create Date: 2026-07-27

Añade ``is_hidden`` a ``chat_threads`` para que el usuario pueda quitar un
hilo del listado sin borrar mensajes ni filas (retención / auditoría).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p60_chat_thread_hidden_01"
down_revision: str | None = "p59_wa_phone_unique_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_threads",
        sa.Column(
            "is_hidden",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_chat_threads_tenant_user_hidden_updated",
        "chat_threads",
        ["tenant_id", "user_id", "is_hidden", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_chat_threads_tenant_user_hidden_updated", table_name="chat_threads")
    op.drop_column("chat_threads", "is_hidden")
