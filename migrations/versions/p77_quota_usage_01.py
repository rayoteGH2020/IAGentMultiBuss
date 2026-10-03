"""Cupos mensuales: tabla ``quota_usage`` y límites mensuales del catálogo (D027).

- ``quota_usage``: consumo por tenant, periodo (día 1 del mes natural, hora de
  España) y código de límite, con ``extra`` = ampliación del SADM solo para ese
  periodo. RLS por tenant; ``saas_app`` sin DELETE ni TRUNCATE (las filas solo
  desaparecen con el tenant, por CASCADE).
- ``plan_entitlements``: añade los límites mensuales de la spec de planes §3 a
  basic / advanced / premium. Los límites diarios actuales siguen hasta que cada
  bloque los sustituya. Los overrides por tenant no se tocan.

Revision ID: p77_quota_usage_01
Revises: p76_llm_budget_01
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p77_quota_usage_01"
down_revision: str | None = "p76_llm_budget_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_LIMITS: dict[str, dict[str, int]] = {
    "basic": {
        "invoices_per_month": 40,
        "tickets_per_month": 30,
        "document_retries_per_month": 40,
        "chat_questions_per_month": 400,
        "contract_uploads_per_month": 5,
        "contract_uploads_first_period": 15,
        "contracts_active_max": 15,
        "contract_max_pages": 100,
    },
    "advanced": {
        "invoices_per_month": 150,
        "tickets_per_month": 80,
        "document_retries_per_month": 150,
        "chat_questions_per_month": 1500,
        "contract_uploads_per_month": 10,
        "contract_uploads_first_period": 40,
        "contracts_active_max": 40,
        "contract_max_pages": 100,
    },
    "premium": {
        "invoices_per_month": 400,
        "tickets_per_month": 200,
        "document_retries_per_month": 400,
        "chat_questions_per_month": 4000,
        "contract_uploads_per_month": 30,
        "contract_uploads_first_period": 100,
        "contracts_active_max": 100,
        "contract_max_pages": 100,
    },
}


def _create_quota_usage() -> None:
    op.create_table(
        "quota_usage",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("extra", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("used >= 0", name="ck_quota_usage_used_non_negative"),
        sa.CheckConstraint("extra >= 0", name="ck_quota_usage_extra_non_negative"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "period", "code"),
    )
    op.execute(text("ALTER TABLE quota_usage ENABLE ROW LEVEL SECURITY;"))
    op.execute(text("ALTER TABLE quota_usage FORCE ROW LEVEL SECURITY;"))
    op.execute(
        text(
            """
            CREATE POLICY tenant_isolation ON quota_usage
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
            """
        )
    )
    op.execute(text("GRANT SELECT, INSERT, UPDATE ON quota_usage TO saas_app"))
    # Los privilegios por defecto del esquema conceden DELETE a tablas nuevas; un
    # contador borrable permitiría "resetear" el cupo. El CASCADE desde tenants no
    # lo necesita (las acciones de FK se ejecutan como propietario de la tabla).
    op.execute(text("REVOKE DELETE, TRUNCATE ON quota_usage FROM saas_app"))


def _insert_limits() -> None:
    conn = op.get_bind()
    for plan_code, limits in _NEW_LIMITS.items():
        for code, value in limits.items():
            conn.execute(
                text(
                    """
                    INSERT INTO plan_entitlements (id, plan_id, kind, code, enabled, limit_value)
                    SELECT CAST(:id AS uuid), p.id, 'limit', CAST(:code AS varchar), NULL,
                           CAST(:value AS numeric)
                    FROM plans AS p
                    WHERE p.code = CAST(:plan_code AS varchar)
                      AND NOT EXISTS (
                          SELECT 1 FROM plan_entitlements AS pe
                          WHERE pe.plan_id = p.id
                            AND pe.kind = 'limit'
                            AND pe.code = CAST(:code AS varchar)
                      )
                    """
                ),
                {"id": str(uuid4()), "plan_code": plan_code, "code": code, "value": value},
            )


def upgrade() -> None:
    _create_quota_usage()
    _insert_limits()


def downgrade() -> None:
    codes = sorted({code for limits in _NEW_LIMITS.values() for code in limits})
    op.get_bind().execute(
        text("DELETE FROM plan_entitlements WHERE kind = 'limit' AND code = ANY(:codes)"),
        {"codes": codes},
    )
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON quota_usage;"))
    op.drop_table("quota_usage")
