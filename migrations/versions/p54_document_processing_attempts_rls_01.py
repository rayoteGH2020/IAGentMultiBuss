"""harden document_processing_attempts RLS

Revision ID: p54_doc_proc_attempts_rls_01
Revises: p30_internal_scheduling_01
Create Date: 2026-07-14

Align document_processing_attempts with the project RLS pattern:
ENABLE + FORCE + USING + WITH CHECK.
Also grants TRUNCATE to saas_app (needed for test cleanup CASCADE).

Nota: el revision id debe caber en alembic_version.version_num (VARCHAR(32)).
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p54_doc_proc_attempts_rls_01"
down_revision: str | None = "p30_internal_scheduling_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "document_processing_attempts"
POLICY_NAME = "tenant_isolation"


def upgrade() -> None:
    op.execute(text(f"ALTER TABLE {TABLE_NAME} ENABLE ROW LEVEL SECURITY;"))
    op.execute(text(f"ALTER TABLE {TABLE_NAME} FORCE ROW LEVEL SECURITY;"))
    op.execute(text(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {TABLE_NAME};"))
    op.execute(
        text(
            f"""
            CREATE POLICY {POLICY_NAME} ON {TABLE_NAME}
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
            """
        )
    )
    # p52 otorgó DML sin TRUNCATE; CASCADE desde tests RLS lo necesita.
    op.execute(
        text(f"GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON {TABLE_NAME} TO saas_app;")
    )


def downgrade() -> None:
    op.execute(text(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {TABLE_NAME};"))
    op.execute(
        text(
            f"""
            CREATE POLICY {POLICY_NAME} ON {TABLE_NAME}
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
            """
        )
    )
    op.execute(text(f"ALTER TABLE {TABLE_NAME} NO FORCE ROW LEVEL SECURITY;"))
