"""grant TRUNCATE to saas_app for test cleanup

Revision ID: p06_grant_truncate_04
Revises: p06_saas_app_03
Create Date: 2026-05-13

Migración independiente para TRUNCATE: permite revertirla sin tocar el resto
de privilegios del rol. Es el último eslabón de la cadena base (p06_*) antes
de que comiencen las migraciones de módulos (p09_*, p10_*…).
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p06_grant_truncate_04"
down_revision: str | None = "p06_saas_app_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # TRUNCATE se gestiona en una migración separada de p06_saas_app_03 porque
    # ALTER DEFAULT PRIVILEGES no cubre TRUNCATE (no es un privilegio por defecto
    # en Postgres). Las tablas creadas DESPUÉS de p06_saas_app_03 no heredan
    # TRUNCATE automáticamente; cada migración de módulo lo concede explícitamente
    # sobre sus tablas. Esta migración cubre las tablas ya existentes en el momento.
    # Su principal consumidor son los tests de integración: pytest usa TRUNCATE
    # para vaciar tablas entre tests, que es más rápido que DELETE + VACUUM.
    op.execute(text("GRANT TRUNCATE ON ALL TABLES IN SCHEMA public TO saas_app"))


def downgrade() -> None:
    op.execute(text("REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM saas_app"))
