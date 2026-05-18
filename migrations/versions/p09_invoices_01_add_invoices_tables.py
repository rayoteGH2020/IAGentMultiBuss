"""add invoices and invoice_lines with RLS

Revision ID: p09_invoices_01
Revises: p06_grant_truncate_04
Create Date: 2026-05-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p09_invoices_01"
down_revision: str | None = "p06_grant_truncate_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

invoice_status = postgresql.ENUM(
    "pending",
    "processing",
    "ready",
    "failed",
    "reviewed",
    name="invoice_status",
    create_type=True,
)


def upgrade() -> None:
    bind = op.get_bind()
    invoice_status.create(bind, checkfirst=True)

    op.create_table(
        "invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            invoice_status,
            server_default=sa.text("'pending'::invoice_status"),
            nullable=False,
        ),
        sa.Column("source_file_key", sa.String(length=500), nullable=True),
        sa.Column("source_mime", sa.String(length=100), nullable=True),
        sa.Column("source_filename", sa.String(length=300), nullable=True),
        sa.Column("fecha", sa.Date(), nullable=True),
        sa.Column("proveedor", sa.String(length=300), nullable=True),
        sa.Column("cif_nif", sa.String(length=20), nullable=True),
        sa.Column("base_imponible", sa.Numeric(12, 2), nullable=True),
        sa.Column("iva_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("iva_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("total", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default="EUR", nullable=False),
        sa.Column("raw_extraction", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("llm_call_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_invoices_tenant_id"), "invoices", ["tenant_id"], unique=False)
    op.create_index("ix_invoices_tenant_status", "invoices", ["tenant_id", "status"], unique=False)
    op.create_index("ix_invoices_tenant_fecha", "invoices", ["tenant_id", "fecha"], unique=False)

    op.create_table(
        "invoice_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.Column("cantidad", sa.Numeric(12, 3), nullable=False),
        sa.Column("precio_unitario", sa.Numeric(12, 4), nullable=False),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_invoice_lines_invoice_id"), "invoice_lines", ["invoice_id"], unique=False)
    op.create_index(op.f("ix_invoice_lines_tenant_id"), "invoice_lines", ["tenant_id"], unique=False)

    for tbl in ("invoices", "invoice_lines"):
        op.execute(
            text(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;")
        )
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
        op.execute(
            text(
                f"GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON {tbl} TO saas_app"
            )
        )


def downgrade() -> None:
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON invoice_lines;"))
    op.execute(text("ALTER TABLE invoice_lines DISABLE ROW LEVEL SECURITY;"))
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON invoices;"))
    op.execute(text("ALTER TABLE invoices DISABLE ROW LEVEL SECURITY;"))

    op.drop_index(op.f("ix_invoice_lines_tenant_id"), table_name="invoice_lines")
    op.drop_index(op.f("ix_invoice_lines_invoice_id"), table_name="invoice_lines")
    op.drop_table("invoice_lines")

    op.drop_index("ix_invoices_tenant_fecha", table_name="invoices")
    op.drop_index("ix_invoices_tenant_status", table_name="invoices")
    op.drop_index(op.f("ix_invoices_tenant_id"), table_name="invoices")
    op.drop_table("invoices")

    invoice_status.drop(op.get_bind(), checkfirst=True)
