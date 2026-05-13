"""enable RLS on tenant tables

Revision ID: p06_rls_02
Revises: p06_identity_01
Create Date: 2026-05-13

"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p06_rls_02"
down_revision: str | None = "p06_identity_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("memberships",)


def upgrade() -> None:
    for tbl in TENANT_TABLES:
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


def downgrade() -> None:
    for tbl in TENANT_TABLES:
        op.execute(text(f"DROP POLICY IF EXISTS tenant_isolation ON {tbl};"))
        op.execute(text(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY;"))
