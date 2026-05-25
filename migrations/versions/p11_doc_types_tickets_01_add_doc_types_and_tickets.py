"""add doc_types catalog and tickets table with RLS

Revision ID: p11_doc_types_tickets_01
Revises: p10_llm_calls_01
Create Date: 2026-05-22

Catálogo global doc_types (factura, ticket) y tabla tickets para recibos
simplificados. Añade doc_type_id a invoices existentes apuntando a factura.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p11_doc_types_tickets_01"
down_revision: str | None = "p10_llm_calls_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ticket_status = postgresql.ENUM(
    "pending",
    "processing",
    "ready",
    "failed",
    "reviewed",
    name="ticket_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    ticket_status.create(bind, checkfirst=True)

    op.create_table(
        "doc_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index(op.f("ix_doc_types_code"), "doc_types", ["code"], unique=True)

    op.execute(
        text(
            """
            INSERT INTO doc_types (id, code, name, description, is_active)
            VALUES
                (gen_random_uuid(), 'factura', 'Factura', 'Factura emitida o recibida', true),
                (gen_random_uuid(), 'ticket', 'Ticket', 'Ticket o recibo simplificado', true);
            """
        )
    )
    op.execute(text("GRANT SELECT ON doc_types TO saas_app"))

    op.add_column(
        "invoices",
        sa.Column("doc_type_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        text(
            """
            UPDATE invoices
            SET doc_type_id = (SELECT id FROM doc_types WHERE code = 'factura')
            WHERE doc_type_id IS NULL;
            """
        )
    )
    op.alter_column("invoices", "doc_type_id", nullable=False)
    op.create_index(op.f("ix_invoices_doc_type_id"), "invoices", ["doc_type_id"], unique=False)
    op.create_foreign_key(
        "fk_invoices_doc_type_id_doc_types",
        "invoices",
        "doc_types",
        ["doc_type_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("doc_type_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            ticket_status,
            server_default=sa.text("'pending'::ticket_status"),
            nullable=False,
        ),
        sa.Column("source_file_key", sa.String(length=500), nullable=True),
        sa.Column("source_mime", sa.String(length=100), nullable=True),
        sa.Column("source_filename", sa.String(length=300), nullable=True),
        sa.Column("fecha", sa.Date(), nullable=True),
        sa.Column("comercio", sa.String(length=300), nullable=True),
        sa.Column("numero_ticket", sa.String(length=100), nullable=True),
        sa.Column("forma_pago", sa.String(length=100), nullable=True),
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
        sa.ForeignKeyConstraint(["doc_type_id"], ["doc_types.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tickets_doc_type_id"), "tickets", ["doc_type_id"], unique=False)
    op.create_index(op.f("ix_tickets_tenant_id"), "tickets", ["tenant_id"], unique=False)
    op.create_index("ix_tickets_tenant_status", "tickets", ["tenant_id", "status"], unique=False)
    op.create_index("ix_tickets_tenant_fecha", "tickets", ["tenant_id", "fecha"], unique=False)

    op.execute(text("ALTER TABLE tickets ENABLE ROW LEVEL SECURITY;"))
    op.execute(text("ALTER TABLE tickets FORCE ROW LEVEL SECURITY;"))
    op.execute(
        text(
            """
            CREATE POLICY tenant_isolation ON tickets
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
            """
        )
    )
    op.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON tickets TO saas_app"))


def downgrade() -> None:
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON tickets;"))
    op.execute(text("ALTER TABLE tickets DISABLE ROW LEVEL SECURITY;"))

    op.drop_index("ix_tickets_tenant_fecha", table_name="tickets")
    op.drop_index("ix_tickets_tenant_status", table_name="tickets")
    op.drop_index(op.f("ix_tickets_tenant_id"), table_name="tickets")
    op.drop_index(op.f("ix_tickets_doc_type_id"), table_name="tickets")
    op.drop_table("tickets")

    op.drop_constraint("fk_invoices_doc_type_id_doc_types", "invoices", type_="foreignkey")
    op.drop_index(op.f("ix_invoices_doc_type_id"), table_name="invoices")
    op.drop_column("invoices", "doc_type_id")

    op.drop_index(op.f("ix_doc_types_code"), table_name="doc_types")
    op.drop_table("doc_types")

    ticket_status.drop(op.get_bind(), checkfirst=True)
