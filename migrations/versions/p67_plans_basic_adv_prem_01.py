"""Restructure commercial plans to basic / advanced / premium (D012).

Revision ID: p67_plans_basic_adv_prem_01
Revises: p66_drop_analytics_ent_01
Create Date: 2026-09-23

Decision D012 (Documentacion_V2/Decision_Log.md):

- Catalogo publicado: Basico (basic), Avanzado (advanced), Premium (premium).
- Basico: documentos + chat docs + knowledge + chat knowledge.
- Avanzado / Premium: mismas features (+ citas internas + WA/TG); Premium = limites
  mayores. Limites duros en los tres (sin null / ilimitado comercial).
- Calendar Google y voz-calendario: codigo conservado, fuera de planes publicados.
- Remap tenants: medium|free -> basic; high -> advanced; total -> premium.
- Codigos legacy medium/high/total quedan is_active=false (historial Stripe).

Entitlements se reescriben desde la matriz de app.core.entitlement_codes.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "p67_plans_basic_adv_prem_01"
down_revision: str | None = "p66_drop_analytics_ent_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PLAN_FEATURES: dict[str, tuple[str, ...]] = {
    "basic": (
        "documents",
        "documents_chat",
        "knowledge",
        "knowledge_chat",
    ),
    "advanced": (
        "documents",
        "documents_chat",
        "knowledge",
        "knowledge_chat",
        "appointments",
        "channel_whatsapp",
        "channel_telegram",
    ),
    "premium": (
        "documents",
        "documents_chat",
        "knowledge",
        "knowledge_chat",
        "appointments",
        "channel_whatsapp",
        "channel_telegram",
    ),
}

_PLAN_LIMITS: dict[str, dict[str, Decimal]] = {
    "basic": {
        "documents_per_day": Decimal("50"),
        "document_retries_per_day": Decimal("20"),
        "knowledge_uploads_per_day": Decimal("25"),
        "knowledge_docs_max": Decimal("100"),
        "chat_messages_per_day": Decimal("100"),
        "channel_messages_per_hour": Decimal("0"),
        "voice_notes_per_hour": Decimal("0"),
        "members_max": Decimal("5"),
        "llm_budget_eur_month": Decimal("30"),
        "channel_external_slots": Decimal("0"),
    },
    "advanced": {
        "documents_per_day": Decimal("200"),
        "document_retries_per_day": Decimal("80"),
        "knowledge_uploads_per_day": Decimal("60"),
        "knowledge_docs_max": Decimal("400"),
        "chat_messages_per_day": Decimal("250"),
        "channel_messages_per_hour": Decimal("80"),
        "voice_notes_per_hour": Decimal("0"),
        "members_max": Decimal("15"),
        "llm_budget_eur_month": Decimal("100"),
        "channel_external_slots": Decimal("2"),
    },
    "premium": {
        "documents_per_day": Decimal("800"),
        "document_retries_per_day": Decimal("300"),
        "knowledge_uploads_per_day": Decimal("200"),
        "knowledge_docs_max": Decimal("1500"),
        "chat_messages_per_day": Decimal("600"),
        "channel_messages_per_hour": Decimal("200"),
        "voice_notes_per_hour": Decimal("0"),
        "members_max": Decimal("40"),
        "llm_budget_eur_month": Decimal("250"),
        "channel_external_slots": Decimal("2"),
    },
}

_PLAN_META: dict[str, tuple[str, str, int]] = {
    "basic": (
        "Basico",
        "Documentos, chat documental, knowledge/RAG y chat sobre knowledge.",
        10,
    ),
    "advanced": (
        "Avanzado",
        "Basico + citas internas (BBDD saas) + WhatsApp/Telegram (chat knowledge).",
        20,
    ),
    "premium": (
        "Premium",
        "Mismas capacidades que Avanzado con limites superiores (duros).",
        30,
    ),
}


def _remap_tenants(conn: sa.Connection) -> None:
    conn.execute(
        text(
            """
            UPDATE tenants
            SET plan_code = 'basic', plan = 'basic', updated_at = now()
            WHERE plan_code IN ('medium', 'free')
               OR (plan_code IS NULL AND plan IN ('medium', 'free'))
            """
        )
    )
    conn.execute(
        text(
            """
            UPDATE tenants
            SET plan_code = 'advanced', plan = 'advanced', updated_at = now()
            WHERE plan_code = 'high' OR plan = 'high'
            """
        )
    )
    conn.execute(
        text(
            """
            UPDATE tenants
            SET plan_code = 'premium', plan = 'premium', updated_at = now()
            WHERE plan_code = 'total' OR plan = 'total'
            """
        )
    )
    conn.execute(
        text(
            """
            UPDATE tenants
            SET plan_code = COALESCE(plan_code, 'basic'),
                plan = COALESCE(NULLIF(plan, ''), plan_code, 'basic'),
                updated_at = now()
            WHERE plan_code IS NULL OR plan_code = ''
            """
        )
    )


def _ensure_plan(
    conn: sa.Connection,
    *,
    code: str,
    name: str,
    description: str,
    sort_order: int,
) -> str:
    row = conn.execute(
        text("SELECT id::text FROM plans WHERE code = :code"),
        {"code": code},
    ).fetchone()
    if row is not None:
        conn.execute(
            text(
                """
                UPDATE plans
                SET name = :name,
                    description = :description,
                    sort_order = :sort_order,
                    is_active = true,
                    is_public = true,
                    updated_at = now()
                WHERE code = :code
                """
            ),
            {
                "code": code,
                "name": name,
                "description": description,
                "sort_order": sort_order,
            },
        )
        return row[0]

    plan_id = str(uuid4())
    conn.execute(
        text(
            """
            INSERT INTO plans (
                id, code, name, description, sort_order,
                is_active, is_public, created_at, updated_at
            ) VALUES (
                CAST(:id AS uuid), :code, :name, :description, :sort_order,
                true, true, now(), now()
            )
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
    return plan_id


def _replace_entitlements(conn: sa.Connection, plan_id: str, plan_code: str) -> None:
    conn.execute(
        text("DELETE FROM plan_entitlements WHERE plan_id = CAST(:plan_id AS uuid)"),
        {"plan_id": plan_id},
    )
    for feature in _PLAN_FEATURES[plan_code]:
        conn.execute(
            text(
                """
                INSERT INTO plan_entitlements (
                    id, plan_id, kind, code, enabled, limit_value
                ) VALUES (
                    CAST(:id AS uuid), CAST(:plan_id AS uuid),
                    'feature', :code, true, NULL
                )
                """
            ),
            {"id": str(uuid4()), "plan_id": plan_id, "code": feature},
        )
    for limit_code, value in _PLAN_LIMITS[plan_code].items():
        conn.execute(
            text(
                """
                INSERT INTO plan_entitlements (
                    id, plan_id, kind, code, enabled, limit_value
                ) VALUES (
                    CAST(:id AS uuid), CAST(:plan_id AS uuid),
                    'limit', :code, NULL, :limit_value
                )
                """
            ),
            {
                "id": str(uuid4()),
                "plan_id": plan_id,
                "code": limit_code,
                "limit_value": value,
            },
        )


def upgrade() -> None:
    conn = op.get_bind()
    _remap_tenants(conn)

    # Rename high -> advanced and total -> premium when target codes absent
    # (preserves stripe_price_id). Otherwise create fresh rows.
    for old_code, new_code in (("high", "advanced"), ("total", "premium")):
        exists_new = conn.execute(
            text("SELECT 1 FROM plans WHERE code = :c"), {"c": new_code}
        ).fetchone()
        exists_old = conn.execute(
            text("SELECT 1 FROM plans WHERE code = :c"), {"c": old_code}
        ).fetchone()
        if exists_old is not None and exists_new is None:
            name, description, sort_order = _PLAN_META[new_code]
            conn.execute(
                text(
                    """
                    UPDATE plans
                    SET code = :new_code,
                        name = :name,
                        description = :description,
                        sort_order = :sort_order,
                        is_active = true,
                        is_public = true,
                        updated_at = now()
                    WHERE code = :old_code
                    """
                ),
                {
                    "old_code": old_code,
                    "new_code": new_code,
                    "name": name,
                    "description": description,
                    "sort_order": sort_order,
                },
            )

    for code, (name, description, sort_order) in _PLAN_META.items():
        plan_id = _ensure_plan(
            conn,
            code=code,
            name=name,
            description=description,
            sort_order=sort_order,
        )
        _replace_entitlements(conn, plan_id, code)

    conn.execute(
        text(
            """
            UPDATE plans
            SET is_active = false, is_public = false, updated_at = now()
            WHERE code IN ('medium', 'high', 'total')
            """
        )
    )


def downgrade() -> None:
    # No restaura entitlements legacy completos; solo reactiva nombres antiguos
    # si siguen existiendo como filas inactivas. Remap de tenants no se revierte.
    conn = op.get_bind()
    conn.execute(
        text(
            """
            UPDATE plans
            SET is_active = true, is_public = true, updated_at = now()
            WHERE code IN ('medium', 'high', 'total')
            """
        )
    )
