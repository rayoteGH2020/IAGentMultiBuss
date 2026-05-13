"""grant TRUNCATE to saas_app for test cleanup

Revision ID: p06_grant_truncate_04
Revises: p06_saas_app_03
Create Date: 2026-05-13

"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p06_grant_truncate_04"
down_revision: str | None = "p06_saas_app_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(text("GRANT TRUNCATE ON ALL TABLES IN SCHEMA public TO saas_app"))


def downgrade() -> None:
    op.execute(text("REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM saas_app"))
