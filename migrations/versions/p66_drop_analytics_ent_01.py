"""Remove analytics entitlement from catalog (modulo 3 no se implementara).

Revision ID: p66_drop_analytics_ent_01
Revises: p65_stripe_billing_01
Create Date: 2026-09-23

Decision D011 (Documentacion_V2/Decision_Log.md): el modulo 3 Analytics SQL
read-only / BI sobre BD externa del cliente queda fuera de alcance de producto
(no se implementara ahora ni como roadmap activo). Motivo: decision comercial —
no se vendra esa capacidad; mantener la feature en plan `total` prometia un
producto inexistente.

Esta migracion:
- borra filas `plan_entitlements` kind=feature code=analytics;
- actualiza la descripcion del plan `total` sin mencionar analytics.

No elimina la columna `usage_meter.analytics_queries_count` (reservada historica,
sin escrituras de producto).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "p66_drop_analytics_ent_01"
down_revision: str | None = "p65_stripe_billing_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TOTAL_DESCRIPTION = (
    "Todo el producto activo: calendario Google, canales, citas y limites altos."
)
_TOTAL_DESCRIPTION_LEGACY = (
    "Todo el producto: analytics, calendario Google y limites altos."
)


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM plan_entitlements
        WHERE kind = 'feature' AND code = 'analytics'
        """
    )
    op.execute(
        f"""
        UPDATE plans
        SET description = '{_TOTAL_DESCRIPTION}',
            updated_at = now()
        WHERE code = 'total'
        """
    )


def downgrade() -> None:
    # Restaura el entitlement historico (no reintroduce producto Analytics).
    op.execute(
        f"""
        UPDATE plans
        SET description = '{_TOTAL_DESCRIPTION_LEGACY}',
            updated_at = now()
        WHERE code = 'total'
        """
    )
    op.execute(
        """
        INSERT INTO plan_entitlements (id, plan_id, kind, code, enabled, limit_value)
        SELECT gen_random_uuid(), p.id, 'feature', 'analytics', true, NULL
        FROM plans p
        WHERE p.code = 'total'
          AND NOT EXISTS (
            SELECT 1 FROM plan_entitlements pe
            WHERE pe.plan_id = p.id AND pe.kind = 'feature' AND pe.code = 'analytics'
          )
        """
    )
