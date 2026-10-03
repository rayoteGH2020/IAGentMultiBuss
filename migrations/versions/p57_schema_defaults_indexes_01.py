"""Alinea server defaults e índices con los modelos ORM.

Cierra la deriva que reportaba `alembic check`:

1. Elimina `users.is_superadmin` (el acceso SADM ya no usa esa columna:
   org Clerk + rol admin + allowlist opcional).
2. Crea el índice `ix_document_processing_attempts_tenant_id` declarado
   por el modelo (`index=True` en tenant_id) y ausente en BD.
3. Elimina el índice único redundante `ix_usage_meter_tenant_period`
   (la PK ya es `(tenant_id, period)`).
4. Añade `server_default` en columnas que los modelos declaran y la BD
   aún no tenía (memberships.role/permissions, users.force_password_reset,
   tenants.plan/settings).

Los demás defaults e índices ya existían en BD; esta migración no los
recrea. La coherencia restante vive en los modelos (`server_default` +
`Index` / `UniqueConstraint` con el mismo nombre que en Postgres).

Revision ID: p57_schema_defaults_idx_01
Revises: p56_doc_limits_charges_01
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "p57_schema_defaults_idx_01"
down_revision: str | None = "p56_doc_limits_charges_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MEMBERSHIP_PERMISSIONS_DEFAULT = (
    "'{\"appointments\": {\"view\": true, \"create\": false, "
    "\"edit\": false, \"cancel\": false}}'::jsonb"
)


def upgrade() -> None:
    # ── 4. Superadmin: la columna quedó huérfana del enfoque antiguo ──────
    op.execute(text("DROP INDEX IF EXISTS ix_users_is_superadmin;"))
    op.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS is_superadmin;"))

    # ── 3. Índice faltante declarado por el modelo ────────────────────────
    op.create_index(
        "ix_document_processing_attempts_tenant_id",
        "document_processing_attempts",
        ["tenant_id"],
        unique=False,
        if_not_exists=True,
    )

    # ── 2. Índice redundante con la PK de usage_meter ─────────────────────
    op.execute(text("DROP INDEX IF EXISTS ix_usage_meter_tenant_period;"))

    # ── 1. Server defaults que faltaban en BD ─────────────────────────────
    op.alter_column(
        "memberships",
        "role",
        existing_type=sa.String(length=32),
        server_default=sa.text("'member'"),
        existing_nullable=False,
    )
    op.execute(
        text(
            f"ALTER TABLE memberships "
            f"ALTER COLUMN permissions SET DEFAULT {_MEMBERSHIP_PERMISSIONS_DEFAULT};"
        )
    )
    op.alter_column(
        "users",
        "force_password_reset",
        existing_type=sa.Boolean(),
        server_default=sa.text("false"),
        existing_nullable=False,
    )
    op.alter_column(
        "tenants",
        "plan",
        existing_type=sa.String(length=32),
        server_default=sa.text("'free'"),
        existing_nullable=False,
    )
    op.execute(text("ALTER TABLE tenants ALTER COLUMN settings SET DEFAULT '{}'::jsonb;"))


def downgrade() -> None:
    op.execute(text("ALTER TABLE tenants ALTER COLUMN settings DROP DEFAULT;"))
    op.alter_column(
        "tenants",
        "plan",
        existing_type=sa.String(length=32),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "users",
        "force_password_reset",
        existing_type=sa.Boolean(),
        server_default=None,
        existing_nullable=False,
    )
    op.execute(text("ALTER TABLE memberships ALTER COLUMN permissions DROP DEFAULT;"))
    op.alter_column(
        "memberships",
        "role",
        existing_type=sa.String(length=32),
        server_default=None,
        existing_nullable=False,
    )

    op.create_index(
        "ix_usage_meter_tenant_period",
        "usage_meter",
        ["tenant_id", "period"],
        unique=True,
    )
    op.drop_index(
        "ix_document_processing_attempts_tenant_id",
        table_name="document_processing_attempts",
    )

    op.add_column(
        "users",
        sa.Column(
            "is_superadmin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index("ix_users_is_superadmin", "users", ["is_superadmin"], unique=False)
