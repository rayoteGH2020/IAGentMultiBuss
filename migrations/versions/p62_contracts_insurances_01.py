"""add contracts and insurances tables with RLS

Revision ID: p62_contracts_insurances_01
Revises: p61_chat_trace_sadm_01
Create Date: 2026-07-29

Catálogo doc_types: contrato y seguro. Tablas contracts e insurances
para extracción estructurada (módulo 1), con RLS y GRANT a saas_app.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p62_contracts_insurances_01"
down_revision: str | None = "p61_chat_trace_sadm_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

contract_status = postgresql.ENUM(
    "pending",
    "processing",
    "ready",
    "failed",
    "reviewed",
    name="contract_status",
    create_type=False,
)

insurance_status = postgresql.ENUM(
    "pending",
    "processing",
    "ready",
    "failed",
    "reviewed",
    name="insurance_status",
    create_type=False,
)


def _create_doc_table(
    table_name: str,
    status_enum: postgresql.ENUM,
    domain_columns: list[sa.Column],
) -> None:
    columns = [
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("doc_type_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            status_enum,
            server_default=sa.text(f"'pending'::{status_enum.name}"),
            nullable=False,
        ),
        sa.Column("source_file_key", sa.String(length=500), nullable=True),
        sa.Column("source_mime", sa.String(length=100), nullable=True),
        sa.Column("source_filename", sa.String(length=300), nullable=True),
        *domain_columns,
        sa.Column("currency", sa.String(length=3), server_default="EUR", nullable=False),
        sa.Column("raw_extraction", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=True),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
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
    ]
    op.create_table(table_name, *columns)
    op.create_index(op.f(f"ix_{table_name}_doc_type_id"), table_name, ["doc_type_id"], unique=False)
    op.create_index(op.f(f"ix_{table_name}_tenant_id"), table_name, ["tenant_id"], unique=False)
    op.create_index(f"ix_{table_name}_tenant_status", table_name, ["tenant_id", "status"])
    op.create_index(
        f"ix_{table_name}_tenant_fecha_inicio",
        table_name,
        ["tenant_id", "fecha_inicio"],
    )
    op.create_index(
        f"ix_{table_name}_tenant_dismissed",
        table_name,
        ["tenant_id", "dismissed_at"],
    )
    op.execute(
        text(
            f"CREATE INDEX ix_{table_name}_error_code ON {table_name} (error_code) "
            "WHERE error_code IS NOT NULL"
        )
    )
    op.execute(text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;"))
    op.execute(text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY;"))
    op.execute(
        text(
            f"""
            CREATE POLICY tenant_isolation ON {table_name}
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
            """
        )
    )
    op.execute(
        text(f"GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON {table_name} TO saas_app")
    )


def _drop_doc_table(table_name: str) -> None:
    op.execute(text(f"DROP POLICY IF EXISTS tenant_isolation ON {table_name};"))
    op.execute(text(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY;"))
    op.execute(text(f"DROP INDEX IF EXISTS ix_{table_name}_error_code;"))
    op.drop_index(f"ix_{table_name}_tenant_dismissed", table_name=table_name)
    op.drop_index(f"ix_{table_name}_tenant_fecha_inicio", table_name=table_name)
    op.drop_index(f"ix_{table_name}_tenant_status", table_name=table_name)
    op.drop_index(op.f(f"ix_{table_name}_tenant_id"), table_name=table_name)
    op.drop_index(op.f(f"ix_{table_name}_doc_type_id"), table_name=table_name)
    op.drop_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()
    contract_status.create(bind, checkfirst=True)
    insurance_status.create(bind, checkfirst=True)

    op.execute(
        text(
            """
            INSERT INTO doc_types (id, code, name, description, is_active)
            VALUES
                (gen_random_uuid(), 'contrato', 'Contrato', 'Contrato de servicio', true),
                (gen_random_uuid(), 'seguro', 'Seguro', 'Seguros', true)
            ON CONFLICT (code) DO NOTHING;
            """
        )
    )

    _create_doc_table(
        "contracts",
        contract_status,
        [
            sa.Column("titulo", sa.String(length=300), nullable=True),
            sa.Column("numero_contrato", sa.String(length=100), nullable=True),
            sa.Column("parte_contraria", sa.String(length=300), nullable=True),
            sa.Column("cif_nif", sa.String(length=50), nullable=True),
            sa.Column("fecha_inicio", sa.Date(), nullable=True),
            sa.Column("fecha_fin", sa.Date(), nullable=True),
            sa.Column("importe", sa.Numeric(12, 2), nullable=True),
            sa.Column("objeto", sa.Text(), nullable=True),
        ],
    )
    _create_doc_table(
        "insurances",
        insurance_status,
        [
            sa.Column("aseguradora", sa.String(length=300), nullable=True),
            sa.Column("numero_poliza", sa.String(length=100), nullable=True),
            sa.Column("tomador", sa.String(length=300), nullable=True),
            sa.Column("cif_nif", sa.String(length=50), nullable=True),
            sa.Column("tipo_seguro", sa.String(length=100), nullable=True),
            sa.Column("fecha_inicio", sa.Date(), nullable=True),
            sa.Column("fecha_fin", sa.Date(), nullable=True),
            sa.Column("prima", sa.Numeric(12, 2), nullable=True),
            sa.Column("cobertura", sa.Text(), nullable=True),
        ],
    )


def downgrade() -> None:
    _drop_doc_table("insurances")
    _drop_doc_table("contracts")
    op.execute(text("DELETE FROM doc_types WHERE code IN ('contrato', 'seguro')"))
    insurance_status.drop(op.get_bind(), checkfirst=True)
    contract_status.drop(op.get_bind(), checkfirst=True)
