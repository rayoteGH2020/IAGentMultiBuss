"""Contratos (bloque 5, D027): altas por cupo, archivo de activos, hash y sustitución.

- ``contract_status`` + ``quota_pending``: contrato guardado pero no encolado por
  falta de altas o de presupuesto de IA.
- ``contracts``:
  - ``file_sha256``: rechaza resubidas del mismo fichero.
  - ``quota_period`` / ``quota_code`` / ``upload_units``: reserva de altas, que se
    devuelve exacta (mes, bolsa y unidades) si el contrato no termina bien.
  - ``page_count``: páginas medidas al subir (1 si es imagen).
  - ``lifecycle``: ``active`` / ``replaced``. Un contrato sustituido libera su
    hueco en el archivo de activos y el chat lo excluye por defecto.

El downgrade no quita ``quota_pending`` del enum: Postgres no permite borrar
valores de un enum (``deploy.sh`` nunca hace downgrade).

Revision ID: p82_contract_quota_01
Revises: p81_chat_quota_01
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "p82_contract_quota_01"
down_revision: str | None = "p81_chat_quota_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LIFECYCLE = postgresql.ENUM("active", "replaced", name="contract_lifecycle", create_type=False)


def upgrade() -> None:
    op.execute(text("ALTER TYPE contract_status ADD VALUE IF NOT EXISTS 'quota_pending'"))
    op.execute(text("CREATE TYPE contract_lifecycle AS ENUM ('active', 'replaced')"))

    op.add_column("contracts", sa.Column("file_sha256", sa.String(length=64), nullable=True))
    op.add_column("contracts", sa.Column("quota_period", sa.Date(), nullable=True))
    op.add_column("contracts", sa.Column("quota_code", sa.String(length=64), nullable=True))
    op.add_column(
        "contracts",
        sa.Column("upload_units", sa.SmallInteger(), nullable=False, server_default=sa.text("1")),
    )
    op.add_column("contracts", sa.Column("page_count", sa.SmallInteger(), nullable=True))
    op.add_column(
        "contracts",
        sa.Column(
            "lifecycle",
            _LIFECYCLE,
            nullable=False,
            server_default=sa.text("'active'::contract_lifecycle"),
        ),
    )
    op.create_index(
        "ix_contracts_tenant_sha256",
        "contracts",
        ["tenant_id", "file_sha256"],
        postgresql_where=sa.text("file_sha256 IS NOT NULL"),
    )
    op.create_index("ix_contracts_tenant_lifecycle", "contracts", ["tenant_id", "lifecycle"])
    op.create_check_constraint(
        "ck_contracts_upload_units", "contracts", "upload_units BETWEEN 1 AND 10"
    )


def downgrade() -> None:
    op.drop_constraint("ck_contracts_upload_units", "contracts", type_="check")
    op.drop_index("ix_contracts_tenant_lifecycle", table_name="contracts")
    op.drop_index("ix_contracts_tenant_sha256", table_name="contracts")
    for column in (
        "lifecycle",
        "page_count",
        "upload_units",
        "quota_code",
        "quota_period",
        "file_sha256",
    ):
        op.drop_column("contracts", column)
    op.execute(text("DROP TYPE contract_lifecycle"))
