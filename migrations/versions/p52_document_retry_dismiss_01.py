"""document retry history and panel dismiss

Revision ID: p52_document_retry_dismiss_01
Revises: p51_invoice_vat_breakdown_01
Create Date: 2026-07-07

- dismissed_at on invoices/tickets (ocultar del panel sin borrar fila)
- document_processing_attempts: historial de ejecuciones por documento
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p52_document_retry_dismiss_01"
down_revision: str | None = "p51_invoice_vat_breakdown_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tickets",
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_invoices_tenant_dismissed",
        "invoices",
        ["tenant_id", "dismissed_at"],
    )
    op.create_index(
        "ix_tickets_tenant_dismissed",
        "tickets",
        ["tenant_id", "dismissed_at"],
    )

    op.create_table(
        "document_processing_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_kind", sa.String(length=16), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("llm_call_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "document_kind",
            "document_id",
            "attempt_number",
            name="uq_document_processing_attempt",
        ),
    )
    op.create_index(
        "ix_doc_proc_attempts_tenant_doc",
        "document_processing_attempts",
        ["tenant_id", "document_kind", "document_id"],
    )

    tbl = "document_processing_attempts"
    op.execute(text(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;"))
    op.execute(text(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY;"))
    op.execute(
        text(
            f"""
            CREATE POLICY tenant_isolation ON {tbl}
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
            """,
        ),
    )
    op.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO saas_app;"))


def downgrade() -> None:
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON document_processing_attempts;"))
    op.drop_index("ix_doc_proc_attempts_tenant_doc", table_name="document_processing_attempts")
    op.drop_table("document_processing_attempts")
    op.drop_index("ix_tickets_tenant_dismissed", table_name="tickets")
    op.drop_index("ix_invoices_tenant_dismissed", table_name="invoices")
    op.drop_column("tickets", "dismissed_at")
    op.drop_column("invoices", "dismissed_at")
