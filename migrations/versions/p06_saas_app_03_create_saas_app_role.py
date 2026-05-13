"""create saas_app role for RLS tests and app connections

Revision ID: p06_saas_app_03
Revises: p06_rls_02
Create Date: 2026-05-13

"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p06_saas_app_03"
down_revision: str | None = "p06_rls_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'saas_app') THEN
                CREATE ROLE saas_app LOGIN PASSWORD 'saas' NOSUPERUSER NOBYPASSRLS INHERIT;
            END IF;
        END
        $$;
        """)
    )
    op.execute(text("GRANT USAGE ON SCHEMA public TO saas_app"))
    op.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public TO saas_app"))
    op.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO saas_app"))
    op.execute(
        text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO saas_app"
        )
    )
    op.execute(
        text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO saas_app"
        )
    )


def downgrade() -> None:
    op.execute(text("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM saas_app"))
    op.execute(text("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM saas_app"))
    op.execute(text("REVOKE USAGE ON SCHEMA public FROM saas_app"))
    op.execute(text("DROP ROLE IF EXISTS saas_app"))
