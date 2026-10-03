"""Retención de ``audit_log`` (Backlog P2c-7): purga por función SECURITY DEFINER.

``audit_log`` es solo inserción para ``saas_app`` (p78). La única forma de borrar
desde la app es ``purge_audit_log(retention_days, batch_size)``, que impone en la
propia BD un mínimo de 365 días: aunque la app se viera comprometida, no podría
borrar el último año de auditoría.

La tabla tiene RLS forzado: la función recorre los tenants fijando
``app.current_tenant`` en cada uno, así funciona aunque el propietario no sea
superusuario ni tenga BYPASSRLS.

Revision ID: p84_audit_log_retention_01
Revises: p83_history_months_01
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p84_audit_log_retention_01"
down_revision: str | None = "p83_history_months_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MIN_RETENTION_DAYS = 365
MAX_PURGE_BATCH = 50_000


def upgrade() -> None:
    op.execute(
        text(
            f"""
            CREATE OR REPLACE FUNCTION purge_audit_log(retention_days integer, batch_size integer)
            RETURNS integer
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = public, pg_temp
            AS $$
            DECLARE
                cutoff timestamptz;
                remaining integer;
                deleted integer := 0;
                n integer;
                t uuid;
            BEGIN
                IF retention_days IS NULL OR retention_days < {MIN_RETENTION_DAYS} THEN
                    RAISE EXCEPTION 'purge_audit_log: retention_days must be >= {MIN_RETENTION_DAYS}'
                        USING ERRCODE = 'invalid_parameter_value';
                END IF;
                IF batch_size IS NULL OR batch_size < 1 OR batch_size > {MAX_PURGE_BATCH} THEN
                    RAISE EXCEPTION 'purge_audit_log: batch_size must be 1..{MAX_PURGE_BATCH}'
                        USING ERRCODE = 'invalid_parameter_value';
                END IF;
                cutoff := now() - make_interval(days => retention_days);
                remaining := batch_size;
                FOR t IN SELECT id FROM tenants ORDER BY id LOOP
                    EXIT WHEN remaining <= 0;
                    PERFORM set_config('app.current_tenant', t::text, true);
                    DELETE FROM audit_log
                    WHERE id IN (
                        SELECT id FROM audit_log
                        WHERE tenant_id = t AND created_at < cutoff
                        ORDER BY created_at
                        LIMIT remaining
                    );
                    GET DIAGNOSTICS n = ROW_COUNT;
                    deleted := deleted + n;
                    remaining := remaining - n;
                END LOOP;
                PERFORM set_config('app.current_tenant', '', true);
                RETURN deleted;
            END;
            $$;
            """
        )
    )
    op.execute(text("REVOKE ALL ON FUNCTION purge_audit_log(integer, integer) FROM PUBLIC;"))
    op.execute(
        text("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'saas_app') THEN
                GRANT EXECUTE ON FUNCTION purge_audit_log(integer, integer) TO saas_app;
            END IF;
        END
        $$;
        """)
    )


def downgrade() -> None:
    op.execute(text("DROP FUNCTION IF EXISTS purge_audit_log(integer, integer);"))
