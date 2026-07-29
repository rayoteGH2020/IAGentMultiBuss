"""internal scheduling tables + membership permissions (Paso 30 Fase A)

Revision ID: p30_internal_scheduling_01
Revises: p53_force_password_reset_01
Create Date: 2026-07-13

Calendario interno multi-profesional: professionals, services, appointments,
horarios, excepciones, permisos JSONB en memberships.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from app.core.scheduling_defaults import (
    DEFAULT_BUSINESS_HOUR_SEEDS,
    DEFAULT_MEMBERSHIP_PERMISSIONS,
    DEFAULT_SCHEDULING_SETTINGS,
)

revision: str = "p30_internal_scheduling_01"
down_revision: str | None = "p53_force_password_reset_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

appointment_status = postgresql.ENUM(
    "scheduled",
    "confirmed",
    "cancelled",
    "completed",
    "no_show",
    name="appointment_status",
    create_type=False,
)

_SCHEDULING_TABLES = (
    "professionals",
    "services",
    "professional_specialties",
    "schedule_exceptions",
    "business_hours",
    "professional_working_hours",
    "appointments",
)


def _enable_rls_and_grant(table: str) -> None:
    op.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;"))
    op.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;"))
    op.execute(
        text(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
            """
        )
    )
    op.execute(
        text(f"GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON {table} TO saas_app")
    )


def _disable_rls(table: str) -> None:
    op.execute(text(f"DROP POLICY IF EXISTS tenant_isolation ON {table};"))
    op.execute(text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;"))


def upgrade() -> None:
    bind = op.get_bind()

    # ── A.0 memberships.permissions ───────────────────────────────────────────
    default_permissions_json = json.dumps(DEFAULT_MEMBERSHIP_PERMISSIONS)
    op.execute(
        text(
            f"""
            ALTER TABLE memberships
            ADD COLUMN IF NOT EXISTS permissions JSONB NOT NULL
            DEFAULT '{default_permissions_json}'::jsonb
            """
        )
    )
    op.execute(
        text(
            f"""
            UPDATE memberships
            SET permissions = '{default_permissions_json}'::jsonb
            WHERE permissions IS NULL
            """
        )
    )
    op.execute(text("ALTER TABLE memberships ALTER COLUMN permissions DROP DEFAULT"))

    # ── A.1 btree_gist ────────────────────────────────────────────────────────
    op.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))

    # ── A.2 professionals ─────────────────────────────────────────────────────
    op.create_table(
        "professionals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("color", sa.String(length=16), nullable=False, server_default="#6366f1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_bookable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_professionals_tenant_id"), "professionals", ["tenant_id"])
    op.create_index(op.f("ix_professionals_user_id"), "professionals", ["user_id"])
    op.create_index(
        "ix_professionals_tenant_active_bookable_sort",
        "professionals",
        ["tenant_id", "is_active", "is_bookable", "sort_order"],
    )
    _enable_rls_and_grant("professionals")

    # ── A.5 services (antes de specialties y appointments) ────────────────────
    op.create_table(
        "services",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_services_tenant_slug"),
        sa.CheckConstraint(
            "duration_minutes >= 15 AND duration_minutes <= 240",
            name="ck_services_duration_minutes",
        ),
    )
    op.create_index(op.f("ix_services_tenant_id"), "services", ["tenant_id"])
    _enable_rls_and_grant("services")

    # ── A.2b professional_specialties ─────────────────────────────────────────
    op.create_table(
        "professional_specialties",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("professional_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["professional_id"], ["professionals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "professional_id",
            "service_id",
            name="uq_professional_specialties_prof_service",
        ),
    )
    op.create_index(
        op.f("ix_professional_specialties_tenant_id"),
        "professional_specialties",
        ["tenant_id"],
    )
    op.create_index(
        op.f("ix_professional_specialties_professional_id"),
        "professional_specialties",
        ["professional_id"],
    )
    op.create_index(
        op.f("ix_professional_specialties_service_id"),
        "professional_specialties",
        ["service_id"],
    )
    _enable_rls_and_grant("professional_specialties")

    # ── A.2c schedule_exceptions ────────────────────────────────────────────
    op.create_table(
        "schedule_exceptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exception_date", sa.Date(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("is_closed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "exception_date", name="uq_schedule_exceptions_date"),
    )
    op.create_index(
        op.f("ix_schedule_exceptions_tenant_id"),
        "schedule_exceptions",
        ["tenant_id"],
    )
    _enable_rls_and_grant("schedule_exceptions")

    # ── A.3 business_hours ────────────────────────────────────────────────────
    op.create_table(
        "business_hours",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("opens_at", sa.Time(), nullable=True),
        sa.Column("closes_at", sa.Time(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "weekday", "sort_order", name="uq_business_hours_slot"),
    )
    op.create_index(op.f("ix_business_hours_tenant_id"), "business_hours", ["tenant_id"])
    _enable_rls_and_grant("business_hours")

    # ── A.4 professional_working_hours ────────────────────────────────────────
    op.create_table(
        "professional_working_hours",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("professional_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("opens_at", sa.Time(), nullable=True),
        sa.Column("closes_at", sa.Time(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["professional_id"], ["professionals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "professional_id",
            "weekday",
            "sort_order",
            name="uq_professional_working_hours_slot",
        ),
    )
    op.create_index(
        op.f("ix_professional_working_hours_tenant_id"),
        "professional_working_hours",
        ["tenant_id"],
    )
    op.create_index(
        op.f("ix_professional_working_hours_professional_id"),
        "professional_working_hours",
        ["professional_id"],
    )
    _enable_rls_and_grant("professional_working_hours")

    # ── A.6 appointments ──────────────────────────────────────────────────────
    appointment_status.create(bind, checkfirst=True)
    op.create_table(
        "appointments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("professional_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            appointment_status,
            nullable=False,
            server_default="scheduled",
        ),
        sa.Column("client_name", sa.String(length=255), nullable=False),
        sa.Column("client_phone", sa.String(length=64), nullable=False),
        sa.Column("client_email", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["professional_id"], ["professionals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_appointments_tenant_id"), "appointments", ["tenant_id"])
    op.create_index(
        op.f("ix_appointments_professional_id"),
        "appointments",
        ["professional_id"],
    )
    op.create_index(op.f("ix_appointments_service_id"), "appointments", ["service_id"])
    op.create_index(
        "ix_appointments_tenant_professional_start",
        "appointments",
        ["tenant_id", "professional_id", "start_at"],
    )
    op.create_index(
        "ix_appointments_tenant_start",
        "appointments",
        ["tenant_id", "start_at"],
    )
    op.execute(
        text(
            """
            ALTER TABLE appointments
            ADD CONSTRAINT ex_appointments_no_overlap
            EXCLUDE USING gist (
                tenant_id WITH =,
                professional_id WITH =,
                tstzrange(start_at, end_at, '[)') WITH &&
            )
            WHERE (professional_id IS NOT NULL AND status <> 'cancelled')
            """
        )
    )
    _enable_rls_and_grant("appointments")

    # ── A.7 scheduling settings + horario default ─────────────────────────────
    scheduling_json = json.dumps(DEFAULT_SCHEDULING_SETTINGS)
    op.execute(
        text(
            f"""
            UPDATE tenants
            SET settings = jsonb_set(
                COALESCE(settings, '{{}}'::jsonb),
                '{{scheduling}}',
                '{scheduling_json}'::jsonb,
                true
            )
            WHERE settings IS NULL
               OR NOT (settings ? 'scheduling')
            """
        )
    )

    for seed in DEFAULT_BUSINESS_HOUR_SEEDS:
        weekday = seed["weekday"]
        sort_order = seed["sort_order"]
        opens_at = seed["opens_at"]
        closes_at = seed["closes_at"]
        op.execute(
            text(
                f"""
                INSERT INTO business_hours (
                    id, tenant_id, weekday, opens_at, closes_at, sort_order,
                    created_at, updated_at
                )
                SELECT
                    gen_random_uuid(),
                    t.id,
                    {weekday},
                    TIME '{opens_at}',
                    TIME '{closes_at}',
                    {sort_order},
                    now(),
                    now()
                FROM tenants t
                WHERE NOT EXISTS (
                    SELECT 1 FROM business_hours bh WHERE bh.tenant_id = t.id
                )
                """
            )
        )


def downgrade() -> None:
    op.execute(text("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS ex_appointments_no_overlap"))

    for table in reversed(_SCHEDULING_TABLES):
        _disable_rls(table)

    op.drop_index("ix_appointments_tenant_start", table_name="appointments")
    op.drop_index("ix_appointments_tenant_professional_start", table_name="appointments")
    op.drop_index(op.f("ix_appointments_service_id"), table_name="appointments")
    op.drop_index(op.f("ix_appointments_professional_id"), table_name="appointments")
    op.drop_index(op.f("ix_appointments_tenant_id"), table_name="appointments")
    op.drop_table("appointments")
    appointment_status.drop(op.get_bind(), checkfirst=True)

    op.drop_index(
        op.f("ix_professional_working_hours_professional_id"),
        table_name="professional_working_hours",
    )
    op.drop_index(
        op.f("ix_professional_working_hours_tenant_id"),
        table_name="professional_working_hours",
    )
    op.drop_table("professional_working_hours")

    op.drop_index(op.f("ix_business_hours_tenant_id"), table_name="business_hours")
    op.drop_table("business_hours")

    op.drop_index(op.f("ix_schedule_exceptions_tenant_id"), table_name="schedule_exceptions")
    op.drop_table("schedule_exceptions")

    op.drop_index(
        op.f("ix_professional_specialties_service_id"),
        table_name="professional_specialties",
    )
    op.drop_index(
        op.f("ix_professional_specialties_professional_id"),
        table_name="professional_specialties",
    )
    op.drop_index(
        op.f("ix_professional_specialties_tenant_id"),
        table_name="professional_specialties",
    )
    op.drop_table("professional_specialties")

    op.drop_index(op.f("ix_services_tenant_id"), table_name="services")
    op.drop_table("services")

    op.drop_index("ix_professionals_tenant_active_bookable_sort", table_name="professionals")
    op.drop_index(op.f("ix_professionals_user_id"), table_name="professionals")
    op.drop_index(op.f("ix_professionals_tenant_id"), table_name="professionals")
    op.drop_table("professionals")

    op.execute(text("ALTER TABLE memberships DROP COLUMN IF EXISTS permissions"))
