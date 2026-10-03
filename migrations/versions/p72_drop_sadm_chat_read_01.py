"""Retira la lectura cross-tenant del superadmin sobre conversaciones de chat.

p61 añadió la política permisiva ``superadmin_select`` en chat_threads y
chat_messages para que la consola SADM listara conversaciones de todos los
tenants. Decisión 2026-09-29: el superadmin no puede leer conversaciones de
ningún tenant salvo el suyo, que ya ve por la política normal de tenant.
Quitar la política hace que la BD lo impida aunque el código activara
``app.superadmin_lookup`` por error.

Se mantiene ``superadmin_select`` en audit_log (sin contenido de mensajes: solo
thread_id y longitudes), que usa la métrica SADM de uso de chat.

Revision ID: p72_drop_sadm_chat_read_01
Revises: p71_revoke_truncate_01
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p72_drop_sadm_chat_read_01"
down_revision: str | None = "p71_revoke_truncate_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHAT_TABLES = ("chat_threads", "chat_messages")


def upgrade() -> None:
    for table in _CHAT_TABLES:
        op.execute(text(f"DROP POLICY IF EXISTS superadmin_select ON {table};"))


def downgrade() -> None:
    for table in _CHAT_TABLES:
        op.execute(
            text(f"""
                CREATE POLICY superadmin_select ON {table}
                AS PERMISSIVE
                FOR SELECT
                USING (current_setting('app.superadmin_lookup', true) = 'true');
            """)
        )
