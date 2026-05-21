"""add invoices and invoice_lines with RLS

Revision ID: p09_invoices_01
Revises: p06_grant_truncate_04
Create Date: 2026-05-14

Crea las tablas del módulo 1 (extracción de facturas) junto con sus índices,
políticas RLS y permisos para saas_app en una sola migración. El patrón
"tabla + RLS + GRANT" en un único upgrade() garantiza que la tabla nunca
existe sin su aislamiento de tenant.
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

# El tipo ENUM se declara aquí (fuera de upgrade/downgrade) para poder
# reutilizarlo tanto en create_table como en drop en downgrade.
# create_type=False: impide que SQLAlchemy intente crear el tipo dentro de
# create_table(); se gestiona manualmente con invoice_status.create() para
# poder usar checkfirst=True y que sea idempotente.
invoice_status = postgresql.ENUM(
    "pending",
    "processing",
    "ready",
    "failed",
    "reviewed",
    name="invoice_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    # checkfirst=True: si la migración se re-ejecuta (p. ej. tras un fallo
    # parcial), no lanza error por tipo ya existente.
    invoice_status.create(bind, checkfirst=True)

    op.create_table(
        "invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            invoice_status,
            # sa.text() necesario porque el cast `::invoice_status` es SQL
            # Postgres-específico que no puede expresarse como valor Python.
            server_default=sa.text("'pending'::invoice_status"),
            nullable=False,
        ),
        # source_* nullable: se rellenan en create_invoice_from_upload; pueden
        # estar vacíos si el Invoice se creó por otra vía.
        sa.Column("source_file_key", sa.String(length=500), nullable=True),
        sa.Column("source_mime", sa.String(length=100), nullable=True),
        sa.Column("source_filename", sa.String(length=300), nullable=True),
        # Campos de extracción nullable: el registro existe antes de que el
        # LLM extraiga los datos; se rellenan al completar apply_extraction_result.
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
        # llm_call_id sin FK declarada: la relación con llm_calls es opcional
        # y de solo referencia; un FK formal forzaría que la llamada LLM
        # exista antes de que el Invoice pueda actualizarse.
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
        # SET NULL: si el revisor es eliminado, la factura se conserva (no CASCADE).
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Índice simple en tenant_id: soporte para queries sin filtro de status.
    op.create_index(op.f("ix_invoices_tenant_id"), "invoices", ["tenant_id"], unique=False)
    # Compuesto tenant+status: query más común ("facturas pending de X tenant").
    op.create_index("ix_invoices_tenant_status", "invoices", ["tenant_id", "status"], unique=False)
    # Compuesto tenant+fecha: filtros por rango de fechas y ordenación.
    op.create_index("ix_invoices_tenant_fecha", "invoices", ["tenant_id", "fecha"], unique=False)

    op.create_table(
        "invoice_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        # tenant_id propio en invoice_lines: RLS no puede hacer JOIN a la tabla
        # padre para obtener el tenant; necesita tenant_id directamente en cada fila.
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False),
        # Numeric(12,3): cantidades fraccionarias (kg, horas, metros).
        sa.Column("cantidad", sa.Numeric(12, 3), nullable=False),
        # Numeric(12,4): precio unitario con alta precisión (por gramo, por cm²).
        sa.Column("precio_unitario", sa.Numeric(12, 4), nullable=False),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        # position preserva el orden de las líneas tal como aparecen en el PDF.
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # CASCADE: borrar la factura padre borra todas sus líneas automáticamente.
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_invoice_lines_invoice_id"), "invoice_lines", ["invoice_id"], unique=False)
    op.create_index(op.f("ix_invoice_lines_tenant_id"), "invoice_lines", ["tenant_id"], unique=False)

    # RLS + GRANT aplicados a ambas tablas con el mismo patrón:
    for tbl in ("invoices", "invoice_lines"):
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
        # GRANT explícito sobre estas tablas: ALTER DEFAULT PRIVILEGES de
        # p06_saas_app_03 cubre tablas futuras, pero este GRANT es la garantía
        # directa para las tablas que crea esta migración.
        op.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON {tbl} TO saas_app")
        )


def downgrade() -> None:
    # Orden: primero eliminar políticas RLS, luego tablas dependientes (lines),
    # luego tablas padre (invoices), y por último el tipo ENUM.
    # Las políticas deben eliminarse antes de deshabilitar RLS para evitar
    # estado inconsistente si el downgrade falla a mitad.
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

    # El tipo ENUM se elimina después de la tabla porque Postgres no permite
    # borrar un tipo mientras alguna columna lo referencia.
    # checkfirst=True: no falla si ya fue eliminado manualmente.
    invoice_status.drop(op.get_bind(), checkfirst=True)
