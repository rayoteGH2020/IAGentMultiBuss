"""SADM chat-trace: superadmin_select on chat + audit_log

Revision ID: p61_chat_trace_sadm_01
Revises: p60_chat_thread_hidden_01
Create Date: 2026-07-28

La consola SuperAdmin necesita listar hilos y mensajes de todos los tenants
(y el audit_log correlacionado). ``saas_app`` es NOBYPASSRLS: se añade la
política permisiva ``superadmin_select`` (solo SELECT), activada con el flag
de sesión ``app.superadmin_lookup`` — el mismo patrón que p56.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p61_chat_trace_sadm_01"
down_revision: str | None = "p60_chat_thread_hidden_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUPERADMIN_POLICY_TABLES = ("chat_threads", "chat_messages", "audit_log")


def upgrade() -> None:
    for table in _SUPERADMIN_POLICY_TABLES:
        op.execute(
            text(f"""
                CREATE POLICY superadmin_select ON {table}
                AS PERMISSIVE
                FOR SELECT
                USING (current_setting('app.superadmin_lookup', true) = 'true');
            """)
        )


def downgrade() -> None:
    for table in _SUPERADMIN_POLICY_TABLES:
        op.execute(text(f"DROP POLICY IF EXISTS superadmin_select ON {table};"))
