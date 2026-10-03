"""Máximo de miembros por plan: 3 / 9 / 20 (D022).

Solo cambia ``members_max`` del catálogo en ``plan_entitlements``; los overrides
por tenant (``tenants.settings['entitlements_override']``) no se tocan. Los
tenants que ya superan el nuevo tope conservan sus miembros: solo se bloquean
las altas nuevas.

Revision ID: p74_members_max_01
Revises: p73_users_phone_01
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p74_members_max_01"
down_revision: str | None = "p73_users_phone_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW: dict[str, int] = {"basic": 3, "advanced": 9, "premium": 20}
_OLD: dict[str, int] = {"basic": 5, "advanced": 15, "premium": 40}


def _set_members_max(values: dict[str, int]) -> None:
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
                  AND pe.code = 'members_max'
                """
            ),
            {"value": value, "plan_code": plan_code},
        )


def upgrade() -> None:
    _set_members_max(_NEW)


def downgrade() -> None:
    _set_members_max(_OLD)
