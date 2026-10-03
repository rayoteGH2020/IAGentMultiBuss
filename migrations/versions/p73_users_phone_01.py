"""Añade users.phone: teléfono de contacto del miembro (D020).

Lo edita el admin del tenant desde la ficha de miembro; no se guarda en Clerk.
Lo usan el mensaje de corte del chat y el aviso al SADM por presupuesto de IA
(teléfono del admin del tenant). ``users`` no tiene RLS: el acceso se limita
por la membresía del tenant en los servicios.

Revision ID: p73_users_phone_01
Revises: p72_drop_sadm_chat_read_01
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p73_users_phone_01"
down_revision: str | None = "p72_drop_sadm_chat_read_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "phone")
