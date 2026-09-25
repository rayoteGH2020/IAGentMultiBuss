"""document error codes, superadmin review policy and processing charges

Revision ID: p56_doc_limits_charges_01
Revises: p55_membership_active_01
Create Date: 2026-07-26

Tres cambios ligados al control de recursos en el procesado documental:

1. `error_code` en invoices, tickets y document_processing_attempts: motivo
   estructurado del fallo, que decide si el reintento está permitido.
2. Política RLS permisiva `superadmin_select`: la consola SADM necesita listar
   documentos rechazados de todos los tenants, y `saas_app` es NOBYPASSRLS.
   Se activa con el flag local a transacción `app.superadmin_lookup`.
3. Tabla `processing_charges`: coste repercutible de los procesados que el
   superadmin autoriza saltándose los límites.

Nota: el revision id debe caber en alembic_version.version_num (VARCHAR(32)).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p56_doc_limits_charges_01"
down_revision: str | None = "p55_membership_active_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUPERADMIN_POLICY_TABLES = ("invoices", "tickets", "llm_calls", "processing_charges")


def upgrade() -> None:
    for table in ("invoices", "tickets", "document_processing_attempts"):
        op.add_column(table, sa.Column("error_code", sa.String(32), nullable=True))

    # Índice parcial: la consola SADM filtra por documentos con motivo de
    # rechazo, que son una fracción mínima del total.
    op.execute(
        text(
            "CREATE INDEX ix_invoices_error_code ON invoices (error_code) "
            "WHERE error_code IS NOT NULL;"
        )
    )
    op.execute(
        text(
            "CREATE INDEX ix_tickets_error_code ON tickets (error_code) "
            "WHERE error_code IS NOT NULL;"
        )
    )

    op.create_table(
        "processing_charges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_kind", sa.String(16), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_eur", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("provider_cost_eur", sa.Numeric(12, 6), nullable=True),
        sa.Column("billable_eur", sa.Numeric(12, 2), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("llm_call_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("authorized_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["authorized_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_processing_charges_tenant_id"),
        "processing_charges",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_processing_charges_tenant_period",
        "processing_charges",
        ["tenant_id", "period"],
        unique=False,
    )
    op.create_index(
        "ix_processing_charges_document",
        "processing_charges",
        ["document_kind", "document_id"],
        unique=False,
    )

    op.execute(text("ALTER TABLE processing_charges ENABLE ROW LEVEL SECURITY;"))
    op.execute(text("ALTER TABLE processing_charges FORCE ROW LEVEL SECURITY;"))
    op.execute(
        text("""
            CREATE POLICY tenant_isolation ON processing_charges
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
        """)
    )
    op.execute(
        text("GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON processing_charges TO saas_app;")
    )

    # Lectura cross-tenant para la consola SADM. Solo SELECT: cualquier
    # escritura sigue exigiendo app.current_tenant, así que el superadmin no
    # puede modificar datos de un tenant sin situarse explícitamente en él.
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

    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON processing_charges;"))
    op.drop_index("ix_processing_charges_document", table_name="processing_charges")
    op.drop_index("ix_processing_charges_tenant_period", table_name="processing_charges")
    op.drop_index(op.f("ix_processing_charges_tenant_id"), table_name="processing_charges")
    op.drop_table("processing_charges")

    op.execute(text("DROP INDEX IF EXISTS ix_tickets_error_code;"))
    op.execute(text("DROP INDEX IF EXISTS ix_invoices_error_code;"))
    for table in ("document_processing_attempts", "tickets", "invoices"):
        op.drop_column(table, "error_code")
