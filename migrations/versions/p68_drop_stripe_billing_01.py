"""Drop Stripe billing columns (tenants.stripe_*, billing_status, plans.stripe_price_id).

Integracion Stripe retirada: el plan lo asigna solo el SADM y el metodo de
cobro esta pendiente de decidir (Backlog_Priorizado). ``tenant_plan_changes``
se conserva: es el historial de asignaciones del SADM.

Revision ID: p68_drop_stripe_billing_01
Revises: p67_plans_basic_adv_prem_01
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p68_drop_stripe_billing_01"
down_revision: str | None = "p67_plans_basic_adv_prem_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_tenants_stripe_subscription_id", table_name="tenants")
    op.drop_index("ix_tenants_stripe_customer_id", table_name="tenants")
    op.drop_column("tenants", "billing_status")
    op.drop_column("tenants", "stripe_subscription_id")
    op.drop_column("tenants", "stripe_customer_id")
    op.drop_column("plans", "stripe_price_id")


def downgrade() -> None:
    # Restaura el esquema; los valores borrados no se recuperan.
    op.add_column("plans", sa.Column("stripe_price_id", sa.String(length=128), nullable=True))
    op.add_column(
        "tenants",
        sa.Column("stripe_customer_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("stripe_subscription_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "billing_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
    )
    op.create_index(
        "ix_tenants_stripe_customer_id", "tenants", ["stripe_customer_id"], unique=True
    )
    op.create_index(
        "ix_tenants_stripe_subscription_id",
        "tenants",
        ["stripe_subscription_id"],
        unique=False,
    )
