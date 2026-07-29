"""add force_password_reset to users (Paso 50 / Paso 24 Fase A)

Revision ID: p53_force_password_reset_01
Revises: p52_document_retry_dismiss_01
Create Date: 2026-07-09

Usuarios creados por SADM deben cambiar contraseña en el primer login.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p53_force_password_reset_01"
down_revision: str | None = "p52_document_retry_dismiss_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        text(
            "ALTER TABLE users "
            "ADD COLUMN IF NOT EXISTS force_password_reset BOOLEAN NOT NULL DEFAULT false"
        )
    )
    op.execute(text("ALTER TABLE users ALTER COLUMN force_password_reset DROP DEFAULT"))


def downgrade() -> None:
    op.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS force_password_reset"))
