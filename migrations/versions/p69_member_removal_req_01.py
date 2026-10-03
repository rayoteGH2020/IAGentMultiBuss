"""Pending removal request on memberships (requested_at + effective date).

Permite mostrar en /settings/members que la baja ya se pidio al SADM y
bloquear una segunda solicitud mientras siga pendiente. ``memberships`` ya
tiene RLS por ``tenant_id``: no requiere politicas nuevas.

Revision ID: p69_member_removal_req_01
Revises: p68_drop_stripe_billing_01
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# alembic_version.version_num es varchar(32): mantener el id corto.
revision: str = "p69_member_removal_req_01"
down_revision: str | None = "p68_drop_stripe_billing_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memberships",
        sa.Column("removal_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "memberships",
        sa.Column("removal_effective_date", sa.Date(), nullable=True),
    )
    # Recupera solicitudes anteriores a esta columna desde audit_log (ultima por
    # membership activa). Con FORCE RLS y sin tenant en sesion no ve filas: no-op.
    op.execute(
        sa.text(
            """
            UPDATE memberships m
            SET removal_requested_at = a.created_at,
                removal_effective_date = (a.metadata->>'effective_date')::date
            FROM (
                SELECT DISTINCT ON (resource_id) resource_id, created_at, metadata
                FROM audit_log
                WHERE action = 'membership.removal_requested'
                  AND metadata ? 'effective_date'
                ORDER BY resource_id, created_at DESC
            ) a
            WHERE m.id = a.resource_id AND m.is_active
            """
        )
    )


def downgrade() -> None:
    op.drop_column("memberships", "removal_effective_date")
    op.drop_column("memberships", "removal_requested_at")
