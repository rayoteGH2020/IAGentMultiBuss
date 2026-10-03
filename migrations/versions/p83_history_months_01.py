"""Histórico visible (bloque 6, D017): ``history_months`` en el catálogo y ``replaced_at``.

- ``plan_entitlements``: ``history_months`` = 12 / 36 / sin límite (NULL). Oculta
  facturas y tickets anteriores al límite; los contratos se rigen por vigencia.
- ``contracts.replaced_at``: cuándo se marcó como sustituido. Para un sustituido
  sin fecha de fin, el histórico se cuenta desde aquí. Los ya sustituidos se
  rellenan con ``updated_at`` (la mejor aproximación disponible).

Revision ID: p83_history_months_01
Revises: p82_contract_quota_01
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "p83_history_months_01"
down_revision: str | None = "p82_contract_quota_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CODE = "history_months"
_VALUES: dict[str, Decimal | None] = {
    "basic": Decimal("12"),
    "advanced": Decimal("36"),
    "premium": None,
}


def upgrade() -> None:
    conn = op.get_bind()
    for plan_code, value in _VALUES.items():
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
            {"id": str(uuid4()), "plan_code": plan_code, "code": _CODE, "value": value},
        )

    op.add_column(
        "contracts", sa.Column("replaced_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        text("UPDATE contracts SET replaced_at = updated_at WHERE lifecycle = 'replaced'")
    )


def downgrade() -> None:
    op.drop_column("contracts", "replaced_at")
    op.get_bind().execute(
        text("DELETE FROM plan_entitlements WHERE kind = 'limit' AND code = :code"),
        {"code": _CODE},
    )
