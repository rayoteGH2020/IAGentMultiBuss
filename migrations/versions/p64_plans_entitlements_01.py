"""Add plans catalog, plan_entitlements seed, and tenants.plan_code.

Revision ID: p64_plans_entitlements_01
Revises: p63_services_notes_01
Create Date: 2026-08-06

Catalogo global sin RLS. Backfill: tenants.plan 'free' -> plan_code 'basic'.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p64_plans_entitlements_01"
down_revision: str | None = "p63_services_notes_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Matriz alineada con app.core.entitlement_codes (seed SQL).
_PLAN_FEATURES: dict[str, tuple[str, ...]] = {
    "basic": ("documents", "documents_chat"),
    "medium": ("documents", "documents_chat", "knowledge", "knowledge_chat"),
    "high": (
        "documents",
        "documents_chat",
        "knowledge",
        "knowledge_chat",
        "appointments",
        "channel_whatsapp",
        "channel_telegram",
    ),
    "total": (
        "documents",
        "documents_chat",
        "knowledge",
        "knowledge_chat",
        "calendar_google",
        "calendar_voice",
        "appointments",
        "channel_whatsapp",
        "channel_telegram",
        "analytics",
    ),
}

_PLAN_LIMITS: dict[str, dict[str, Decimal | None]] = {
    "basic": {
        "documents_per_day": Decimal("30"),
        "document_retries_per_day": Decimal("10"),
        "knowledge_uploads_per_day": Decimal("0"),
        "knowledge_docs_max": Decimal("0"),
        "chat_messages_per_day": Decimal("40"),
        "channel_messages_per_hour": Decimal("0"),
        "voice_notes_per_hour": Decimal("0"),
        "members_max": Decimal("3"),
        "llm_budget_eur_month": Decimal("5"),
        "channel_external_slots": Decimal("0"),
    },
    "medium": {
        "documents_per_day": Decimal("100"),
        "document_retries_per_day": Decimal("30"),
        "knowledge_uploads_per_day": Decimal("20"),
        "knowledge_docs_max": Decimal("50"),
        "chat_messages_per_day": Decimal("80"),
        "channel_messages_per_hour": Decimal("0"),
        "voice_notes_per_hour": Decimal("0"),
        "members_max": Decimal("10"),
        "llm_budget_eur_month": Decimal("25"),
        "channel_external_slots": Decimal("0"),
    },
    "high": {
        "documents_per_day": Decimal("300"),
        "document_retries_per_day": Decimal("100"),
        "knowledge_uploads_per_day": Decimal("50"),
        "knowledge_docs_max": Decimal("200"),
        "chat_messages_per_day": Decimal("150"),
        "channel_messages_per_hour": Decimal("60"),
        "voice_notes_per_hour": Decimal("0"),
        "members_max": Decimal("25"),
        "llm_budget_eur_month": Decimal("80"),
        "channel_external_slots": Decimal("2"),
    },
    "total": {
        "documents_per_day": Decimal("1000"),
        "document_retries_per_day": Decimal("500"),
        "knowledge_uploads_per_day": Decimal("200"),
        "knowledge_docs_max": Decimal("1000"),
        "chat_messages_per_day": Decimal("400"),
        "channel_messages_per_hour": Decimal("120"),
        "voice_notes_per_hour": Decimal("60"),
        "members_max": Decimal("100"),
        "llm_budget_eur_month": None,
        "channel_external_slots": Decimal("2"),
    },
}

_PLAN_META: dict[str, tuple[str, str, int]] = {
    "basic": ("Basico", "Documentos ligeros y chat documental basico.", 10),
    "medium": ("Medio", "Basico + knowledge/RAG y chat sobre knowledge.", 20),
    "high": ("Alto", "Medio + citas y canales externos.", 30),
    "total": (
        "Total",
        "Todo el producto: analytics, calendario Google y limites altos.",
        40,
    ),
}


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_public", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("stripe_price_id", sa.String(length=128), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="plans_code_key"),
    )
    op.create_index("ix_plans_code", "plans", ["code"], unique=True)

    op.create_table(
        "plan_entitlements",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("limit_value", sa.Numeric(14, 4), nullable=True),
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
        sa.CheckConstraint("kind IN ('feature', 'limit')", name="plan_entitlements_kind_check"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_id",
            "kind",
            "code",
            name="plan_entitlements_plan_kind_code_key",
        ),
    )
    op.create_index("ix_plan_entitlements_plan_id", "plan_entitlements", ["plan_id"])

    op.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON plans TO saas_app"))
    op.execute(
        text("GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON plan_entitlements TO saas_app")
    )

    conn = op.get_bind()
    for code, (name, description, sort_order) in _PLAN_META.items():
        plan_id = conn.execute(text("SELECT gen_random_uuid()")).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO plans (id, code, name, description, sort_order, is_active, is_public)
                VALUES (:id, :code, :name, :description, :sort_order, true, true)
                """
            ),
            {
                "id": plan_id,
                "code": code,
                "name": name,
                "description": description,
                "sort_order": sort_order,
            },
        )
        for feature in _PLAN_FEATURES[code]:
            conn.execute(
                text(
                    """
                    INSERT INTO plan_entitlements (id, plan_id, kind, code, enabled, limit_value)
                    VALUES (gen_random_uuid(), :plan_id, 'feature', :code, true, NULL)
                    """
                ),
                {"plan_id": plan_id, "code": feature},
            )
        for limit_code, value in _PLAN_LIMITS[code].items():
            conn.execute(
                text(
                    """
                    INSERT INTO plan_entitlements (id, plan_id, kind, code, enabled, limit_value)
                    VALUES (gen_random_uuid(), :plan_id, 'limit', :code, NULL, :limit_value)
                    """
                ),
                {"plan_id": plan_id, "code": limit_code, "limit_value": value},
            )

    op.add_column(
        "tenants",
        sa.Column(
            "plan_code",
            sa.String(length=32),
            server_default=sa.text("'basic'"),
            nullable=False,
        ),
    )
    op.create_index("ix_tenants_plan_code", "tenants", ["plan_code"], unique=False)

    op.execute(
        text(
            """
            UPDATE tenants
            SET plan_code = CASE
                WHEN lower(plan) IN ('basic', 'medium', 'high', 'total') THEN lower(plan)
                WHEN lower(plan) = 'free' THEN 'basic'
                ELSE 'basic'
            END
            """
        )
    )
    # Compatibilidad: alinear etiqueta legacy `free` con el catalogo.
    op.execute(text("UPDATE tenants SET plan = 'basic' WHERE lower(plan) = 'free'"))
    op.alter_column(
        "tenants",
        "plan",
        server_default=sa.text("'basic'"),
        existing_type=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "tenants",
        "plan",
        server_default=sa.text("'free'"),
        existing_type=sa.String(length=32),
        existing_nullable=False,
    )
    op.drop_index("ix_tenants_plan_code", table_name="tenants")
    op.drop_column("tenants", "plan_code")
    op.drop_table("plan_entitlements")
    op.drop_index("ix_plans_code", table_name="plans")
    op.drop_table("plans")
