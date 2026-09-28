"""Nombres visibles de planes unificados: Básico / Avanzado / Premium.

``plans.name`` / ``plans.description`` quedaron de p67 como "Basico...". La UI
usa ``plan_ui_name()`` (entitlement_codes.PLAN_UI_NAMES); esta migración deja la
BD igual para cualquier lectura directa. Valores fijados aquí a propósito: una
migración no debe cambiar si el código evoluciona.

Revision ID: p70_plan_ui_names_01
Revises: p69_member_removal_req_01
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p70_plan_ui_names_01"
down_revision: str | None = "p69_member_removal_req_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW: dict[str, tuple[str, str]] = {
    "basic": (
        "Básico",
        "Documentos, chat documental, knowledge/RAG y chat sobre knowledge.",
    ),
    "advanced": (
        "Avanzado",
        "Básico + citas internas (BBDD saas) + WhatsApp/Telegram (chat knowledge).",
    ),
    "premium": (
        "Premium",
        "Mismas capacidades que Avanzado con limites superiores (duros).",
    ),
}
_OLD: dict[str, tuple[str, str]] = {
    "basic": (
        "Basico",
        "Documentos, chat documental, knowledge/RAG y chat sobre knowledge.",
    ),
    "advanced": (
        "Avanzado",
        "Basico + citas internas (BBDD saas) + WhatsApp/Telegram (chat knowledge).",
    ),
    "premium": (
        "Premium",
        "Mismas capacidades que Avanzado con limites superiores (duros).",
    ),
}

_UPDATE = sa.text("UPDATE plans SET name = :name, description = :description WHERE code = :code")


def _apply(values: dict[str, tuple[str, str]]) -> None:
    for code, (name, description) in values.items():
        op.execute(_UPDATE.bindparams(code=code, name=name, description=description))


def upgrade() -> None:
    _apply(_NEW)


def downgrade() -> None:
    _apply(_OLD)
