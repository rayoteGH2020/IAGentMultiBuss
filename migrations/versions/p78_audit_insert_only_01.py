"""audit_log solo inserción para el rol de aplicación saas_app.

``p16_audit_01`` concedió UPDATE y DELETE sobre ``audit_log`` a ``saas_app``. Con
una SQLi o un proceso comprometido se podrían borrar o reescribir las huellas del
propio tenant. La app solo inserta y lee (``audit_service.log_action``).

Las acciones de FK siguen funcionando: el borrado de un tenant (ON DELETE CASCADE)
y el de un usuario (ON DELETE SET NULL) los ejecuta Postgres con los permisos del
propietario de ``audit_log``, no con los de ``saas_app``.

Las migraciones nuevas no deben volver a conceder UPDATE/DELETE sobre
``audit_log`` a ``saas_app``; lo vigila tests/unit/test_migrations_audit_insert_only.py.

Revision ID: p78_audit_insert_only_01
Revises: p77_quota_usage_01
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p78_audit_insert_only_01"
down_revision: str | None = "p77_quota_usage_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        text("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'saas_app') THEN
                REVOKE UPDATE, DELETE ON audit_log FROM saas_app;
            END IF;
        END
        $$;
        """)
    )


def downgrade() -> None:
    op.execute(
        text("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'saas_app') THEN
                GRANT UPDATE, DELETE ON audit_log TO saas_app;
            END IF;
        END
        $$;
        """)
    )
