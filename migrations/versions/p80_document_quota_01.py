"""Cupo mensual de facturas y tickets, duplicados por hash y reintentos (bloques 2 y 3).

- ``invoice_status`` / ``ticket_status`` + ``quota_pending``: documento guardado
  pero no encolado por falta de cupo o de presupuesto de IA (spec planes §4.2).
- ``invoices`` / ``tickets``: ``file_sha256`` (resubidas del mismo fichero sin
  LLM ni cupo) y ``quota_period`` (mes de la reserva de cupo; NULL = sin reserva).
- ``invoices`` / ``tickets`` / ``contracts`` / ``insurances``: ``manual_retry_count``
  (máximo 3 reintentos manuales por documento, D027).
- Se retira ``document_retries_per_day`` del catálogo: lo sustituye
  ``document_retries_per_month`` (ya en el catálogo desde p77).

El downgrade no quita ``quota_pending`` de los enums: Postgres no permite borrar
valores de un enum (``deploy.sh`` nunca hace downgrade).

Revision ID: p80_document_quota_01
Revises: p79_activity_log_01
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "p80_document_quota_01"
down_revision: str | None = "p79_activity_log_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_QUOTA_TABLES = ("invoices", "tickets")
_RETRY_TABLES = ("invoices", "tickets", "contracts", "insurances")
_RETIRED_LIMIT = "document_retries_per_day"


def upgrade() -> None:
    op.execute(text("ALTER TYPE invoice_status ADD VALUE IF NOT EXISTS 'quota_pending'"))
    op.execute(text("ALTER TYPE ticket_status ADD VALUE IF NOT EXISTS 'quota_pending'"))

    for table in _QUOTA_TABLES:
        op.add_column(table, sa.Column("file_sha256", sa.String(length=64), nullable=True))
        op.add_column(table, sa.Column("quota_period", sa.Date(), nullable=True))
        op.create_index(
            f"ix_{table}_tenant_sha256",
            table,
            ["tenant_id", "file_sha256"],
            postgresql_where=sa.text("file_sha256 IS NOT NULL"),
        )
    for table in _RETRY_TABLES:
        op.add_column(
            table,
            sa.Column(
                "manual_retry_count",
                sa.SmallInteger(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )

    op.get_bind().execute(
        text("DELETE FROM plan_entitlements WHERE kind = 'limit' AND code = :code"),
        {"code": _RETIRED_LIMIT},
    )


def downgrade() -> None:
    for table in _RETRY_TABLES:
        op.drop_column(table, "manual_retry_count")
    for table in _QUOTA_TABLES:
        op.drop_index(f"ix_{table}_tenant_sha256", table_name=table)
        op.drop_column(table, "quota_period")
        op.drop_column(table, "file_sha256")
