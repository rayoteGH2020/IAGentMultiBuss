"""Stripe billing fields on tenants + tenant_plan_changes history.

Revision ID: p65_stripe_billing_01
Revises: p64_plans_entitlements_01
Create Date: 2026-09-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p65_stripe_billing_01"
down_revision: str | None = "p64_plans_entitlements_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
        "ix_tenants_stripe_customer_id",
        "tenants",
        ["stripe_customer_id"],
        unique=True,
    )
    op.create_index(
        "ix_tenants_stripe_subscription_id",
        "tenants",
        ["stripe_subscription_id"],
        unique=False,
    )

    op.create_table(
        "tenant_plan_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_plan_code", sa.String(length=32), nullable=True),
        sa.Column("to_plan_code", sa.String(length=32), nullable=False),
        sa.Column("changed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_tenant_plan_changes_tenant_id"),
        "tenant_plan_changes",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_tenant_plan_changes_tenant_created",
        "tenant_plan_changes",
        ["tenant_id", "created_at"],
        unique=False,
    )

    op.execute(text("ALTER TABLE tenant_plan_changes ENABLE ROW LEVEL SECURITY;"))
    op.execute(text("ALTER TABLE tenant_plan_changes FORCE ROW LEVEL SECURITY;"))
    op.execute(
        text(
            """
            CREATE POLICY tenant_isolation ON tenant_plan_changes
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
            """
        )
    )
    op.execute(
        text(
            "GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON tenant_plan_changes TO saas_app"
        )
    )


def downgrade() -> None:
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON tenant_plan_changes;"))
    op.execute(text("ALTER TABLE tenant_plan_changes DISABLE ROW LEVEL SECURITY;"))
    op.drop_index("ix_tenant_plan_changes_tenant_created", table_name="tenant_plan_changes")
    op.drop_index(op.f("ix_tenant_plan_changes_tenant_id"), table_name="tenant_plan_changes")
    op.drop_table("tenant_plan_changes")

    op.drop_index("ix_tenants_stripe_subscription_id", table_name="tenants")
    op.drop_index("ix_tenants_stripe_customer_id", table_name="tenants")
    op.drop_column("tenants", "billing_status")
    op.drop_column("tenants", "stripe_subscription_id")
    op.drop_column("tenants", "stripe_customer_id")
