"""add llm_calls with RLS

Revision ID: p10_llm_calls_01
Revises: p09_invoices_01
Create Date: 2026-05-18

Tabla de observabilidad append-only: registra cada llamada a un LLM con
metadatos de coste, latencia y resultado. Va después de invoices porque
Invoice.llm_call_id referencia esta tabla conceptualmente (aunque sin FK
formal para evitar dependencia cíclica de ciclo de vida).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p10_llm_calls_01"
down_revision: str | None = "p09_invoices_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        # task y status son String (no ENUM) para ser extensibles sin migración
        # cuando se añadan nuevos módulos o estados al sistema.
        sa.Column("task", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        # prompt_version nullable: las llamadas de embedding no usan prompts versionados.
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        # server_default="0" en métricas numéricas: si el registro se crea al
        # inicio de la llamada (antes de tener los conteos), Postgres garantiza 0.
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        # Numeric(10,6): 6 decimales para coste porque las llamadas individuales
        # cuestan del orden de 0.000050 EUR; necesitamos precisión para billing.
        sa.Column(
            "cost_eur",
            sa.Numeric(10, 6),
            server_default="0",
            nullable=False,
        ),
        sa.Column("latency_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            # El cast `::character varying` es necesario en server_default para
            # que Postgres lo acepte como literal de tipo varchar.
            server_default=sa.text("'ok'::character varying"),
            nullable=False,
        ),
        # error Text (sin límite): trazas y mensajes de error del LLM pueden
        # ser muy extensos; aquí no se trunca a diferencia de invoice.error_message.
        sa.Column("error", sa.Text(), nullable=True),
        # langfuse_trace_id: enlace al sistema de trazas LLM self-hosted para
        # inspección detallada de prompts, tokens y timing.
        sa.Column("langfuse_trace_id", sa.String(length=100), nullable=True),
        # Sin updated_at: los registros son inmutables (append-only). Un LLMCall
        # nunca se modifica después de ser creado.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Índice simple en tenant_id: soporte para queries sin filtro temporal.
    op.create_index(op.f("ix_llm_calls_tenant_id"), "llm_calls", ["tenant_id"], unique=False)
    # Índice compuesto tenant+created_at: cubre el endpoint de métricas
    # (WHERE tenant_id AND created_at >= since) y las queries de billing por período.
    op.create_index(
        "ix_llm_calls_tenant_created", "llm_calls", ["tenant_id", "created_at"], unique=False
    )

    tbl = "llm_calls"
    # Mismo patrón RLS que invoices: ENABLE + FORCE + política + GRANT.
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
    # GRANT explícito sobre esta tabla; cubre el caso en que ALTER DEFAULT
    # PRIVILEGES de p06_saas_app_03 no aplica (ejecutado con otro rol).
    op.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON {tbl} TO saas_app"))


def downgrade() -> None:
    # Primero políticas RLS, luego índices, luego tabla.
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON llm_calls;"))
    op.execute(text("ALTER TABLE llm_calls DISABLE ROW LEVEL SECURITY;"))

    op.drop_index("ix_llm_calls_tenant_created", table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_tenant_id"), table_name="llm_calls")
    op.drop_table("llm_calls")
