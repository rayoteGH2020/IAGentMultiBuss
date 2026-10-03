"""Revoca TRUNCATE al rol de aplicación saas_app.

TRUNCATE no pasa por las políticas RLS: con una SQLi o un proceso comprometido,
saas_app podría vaciar las tablas de todos los tenants. DELETE sí queda acotado
por RLS. Ningún test ni código de aplicación usa TRUNCATE (los tests limpian con
DELETE acotado), así que el permiso sobraba desde p06_grant_truncate_04.

Las migraciones nuevas no deben volver a conceder TRUNCATE a saas_app; lo
vigila tests/unit/test_migrations_no_truncate_grant.py.

Revision ID: p71_revoke_truncate_01
Revises: p70_plan_ui_names_01
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p71_revoke_truncate_01"
down_revision: str | None = "p70_plan_ui_names_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        text("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'saas_app') THEN
                REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM saas_app;
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
                GRANT TRUNCATE ON ALL TABLES IN SCHEMA public TO saas_app;
            END IF;
        END
        $$;
        """)
    )
