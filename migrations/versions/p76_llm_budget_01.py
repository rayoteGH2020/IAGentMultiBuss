"""Presupuesto mensual de IA por plan: 6 / 15 / 30 EUR (D026).

Solo cambia ``llm_budget_eur_month`` del catálogo en ``plan_entitlements``; los
overrides por tenant (``tenants.settings['entitlements_override']``) no se
tocan. Un tenant que ya haya gastado más del nuevo tope este mes queda
bloqueado para la IA hasta el siguiente periodo o hasta un override del SADM.

Revision ID: p76_llm_budget_01
Revises: p75_contract_amounts_01
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p76_llm_budget_01"
down_revision: str | None = "p75_contract_amounts_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW: dict[str, int] = {"basic": 6, "advanced": 15, "premium": 30}
_OLD: dict[str, int] = {"basic": 30, "advanced": 100, "premium": 250}


def _set_llm_budget(values: dict[str, int]) -> None:
    conn = op.get_bind()
    for plan_code, value in values.items():
        conn.execute(
            text(
                """
                UPDATE plan_entitlements AS pe
                SET limit_value = :value, updated_at = now()
                FROM plans AS p
                WHERE pe.plan_id = p.id
                  AND p.code = :plan_code
                  AND pe.kind = 'limit'
                  AND pe.code = 'llm_budget_eur_month'
                """
            ),
            {"value": value, "plan_code": plan_code},
        )


def upgrade() -> None:
    _set_llm_budget(_NEW)


def downgrade() -> None:
    _set_llm_budget(_OLD)
