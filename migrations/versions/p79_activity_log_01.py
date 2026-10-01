"""activity_log: registro de actividad en BD para depurar el piloto (D029).

- Tabla sin FK (inserción en bloque desde un buffer por proceso) e índices por
  fecha, tenant y correlación (``request_id``, ``job_id``, ``parent_request_id``).
- ``saas_app`` solo INSERT: no lee, no modifica ni borra. La consulta se hace por
  SQL con el rol propietario.
- RLS activado con ``tenant_isolation`` (AGENTS.md §7) y una política de INSERT
  permisiva: el volcado mezcla filas de varios tenants en un mismo INSERT. Sin
  FORCE: el propietario (consultas y purga) no queda sujeto a RLS.
- Purga: ``purge_activity_log(retention_days, batch_size)`` SECURITY DEFINER, con
  un mínimo de 7 días que no depende de quien la llame. Solo ``saas_app`` la ejecuta.

Revision ID: p79_activity_log_01
Revises: p78_audit_insert_only_01
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p79_activity_log_01"
down_revision: str | None = "p78_audit_insert_only_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MIN_RETENTION_DAYS = 7
MAX_PURGE_BATCH = 100_000


def _create_table() -> None:
    op.create_table(
        "activity_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("level", sa.String(length=10), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_id", sa.String(length=128), nullable=True),
        sa.Column("parent_request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("method", sa.String(length=8), nullable=True),
        sa.Column("status_code", sa.SmallInteger(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("htmx", sa.Boolean(), nullable=True),
        sa.Column("attempt", sa.SmallInteger(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "kind IN ('request', 'job', 'event', 'error')", name="ck_activity_log_kind"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_activity_log_occurred_at", "activity_log", ["occurred_at"])
    op.create_index("ix_activity_log_tenant_occurred", "activity_log", ["tenant_id", "occurred_at"])
    for column in ("request_id", "job_id", "parent_request_id"):
        op.create_index(
            f"ix_activity_log_{column}",
            "activity_log",
            [column],
            postgresql_where=sa.text(f"{column} IS NOT NULL"),
        )


def _secure_table() -> None:
    op.execute(text("ALTER TABLE activity_log ENABLE ROW LEVEL SECURITY;"))
    op.execute(
        text(
            """
            CREATE POLICY tenant_isolation ON activity_log
            USING (tenant_id::text = current_setting('app.current_tenant', true));
            """
        )
    )
    op.execute(text("CREATE POLICY activity_insert ON activity_log FOR INSERT WITH CHECK (true);"))
    op.execute(
        text("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'saas_app') THEN
                -- Los privilegios por defecto del esquema conceden SELECT, UPDATE y
                -- DELETE a tablas nuevas: se retiran todos y se deja solo INSERT.
                REVOKE ALL ON activity_log FROM saas_app;
                GRANT INSERT ON activity_log TO saas_app;
            END IF;
        END
        $$;
        """)
    )


def _create_purge_function() -> None:
    op.execute(
        text(
            f"""
            CREATE OR REPLACE FUNCTION purge_activity_log(retention_days integer, batch_size integer)
            RETURNS integer
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = public, pg_temp
            AS $$
            DECLARE
                deleted integer;
            BEGIN
                IF retention_days IS NULL OR retention_days < {MIN_RETENTION_DAYS} THEN
                    RAISE EXCEPTION 'purge_activity_log: retention_days must be >= {MIN_RETENTION_DAYS}'
                        USING ERRCODE = 'invalid_parameter_value';
                END IF;
                IF batch_size IS NULL OR batch_size < 1 OR batch_size > {MAX_PURGE_BATCH} THEN
                    RAISE EXCEPTION 'purge_activity_log: batch_size must be 1..{MAX_PURGE_BATCH}'
                        USING ERRCODE = 'invalid_parameter_value';
                END IF;
                DELETE FROM activity_log
                WHERE id IN (
                    SELECT id FROM activity_log
                    WHERE occurred_at < now() - make_interval(days => retention_days)
                    ORDER BY id
                    LIMIT batch_size
                );
                GET DIAGNOSTICS deleted = ROW_COUNT;
                RETURN deleted;
            END;
            $$;
            """
        )
    )
    op.execute(text("REVOKE ALL ON FUNCTION purge_activity_log(integer, integer) FROM PUBLIC;"))
    op.execute(
        text("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'saas_app') THEN
                GRANT EXECUTE ON FUNCTION purge_activity_log(integer, integer) TO saas_app;
            END IF;
        END
        $$;
        """)
    )


def upgrade() -> None:
    _create_table()
    _secure_table()
    _create_purge_function()


def downgrade() -> None:
    op.execute(text("DROP FUNCTION IF EXISTS purge_activity_log(integer, integer);"))
    op.drop_table("activity_log")
