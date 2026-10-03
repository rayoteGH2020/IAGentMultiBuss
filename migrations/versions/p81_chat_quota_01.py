"""Chat (bloque 4, D023): se retira ``chat_messages_per_day`` del catálogo.

Lo sustituyen ``chat_questions_per_month`` (en el catálogo desde p77, ahora
aplicado) y el límite de ritmo por usuario (``CHAT_RATE_LIMIT_PER_MINUTE`` /
``CHAT_RATE_LIMIT_PER_HOUR``). Los overrides por tenant no se tocan: el código
sigue siendo válido aunque ya no se aplica.

Revision ID: p81_chat_quota_01
Revises: p80_document_quota_01
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from uuid import uuid4

from alembic import op
from sqlalchemy import text

revision: str = "p81_chat_quota_01"
down_revision: str | None = "p80_document_quota_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RETIRED_LIMIT = "chat_messages_per_day"
# Valores del seed anterior, solo para el downgrade.
_PREVIOUS: dict[str, Decimal] = {
    "basic": Decimal("100"),
    "advanced": Decimal("250"),
    "premium": Decimal("600"),
}


def upgrade() -> None:
    op.get_bind().execute(
        text("DELETE FROM plan_entitlements WHERE kind = 'limit' AND code = :code"),
        {"code": _RETIRED_LIMIT},
    )


def downgrade() -> None:
    conn = op.get_bind()
    for plan_code, value in _PREVIOUS.items():
        conn.execute(
            text(
                """
                INSERT INTO plan_entitlements (id, plan_id, kind, code, enabled, limit_value)
                SELECT CAST(:id AS uuid), p.id, 'limit', CAST(:code AS varchar), NULL,
                       CAST(:value AS numeric)
                FROM plans AS p
                WHERE p.code = CAST(:plan_code AS varchar)
                """
            ),
            {"id": str(uuid4()), "code": _RETIRED_LIMIT, "value": value, "plan_code": plan_code},
        )
