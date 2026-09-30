"""Contratos: fecha de firma e importes separados por periodicidad (P2b-3).

Sustituye ``importe`` (mezclaba cuota mensual, renta anual y total) por
``importe_periodico`` + ``periodicidad``, ``importe_total`` e ``importe_anual``
(calculado por el servicio), más ``fecha_firma`` e ``iva_incluido``.

``importe`` se elimina sin traducirlo: su valor era ambiguo. No hay producción;
en dev se vuelven a extraer los contratos con ``contract_extraction_v2``
(``scripts/reextract_contracts.py``). El downgrade recupera la columna vacía.

Revision ID: p75_contract_amounts_01
Revises: p74_members_max_01
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p75_contract_amounts_01"
down_revision: str | None = "p74_members_max_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AMOUNT = sa.Numeric(12, 2)


def upgrade() -> None:
    op.add_column("contracts", sa.Column("fecha_firma", sa.Date(), nullable=True))
    op.add_column("contracts", sa.Column("importe_periodico", _AMOUNT, nullable=True))
    op.add_column("contracts", sa.Column("periodicidad", sa.String(length=20), nullable=True))
    op.add_column("contracts", sa.Column("importe_total", _AMOUNT, nullable=True))
    op.add_column("contracts", sa.Column("importe_anual", _AMOUNT, nullable=True))
    op.add_column("contracts", sa.Column("iva_incluido", sa.Boolean(), nullable=True))
    op.create_check_constraint(
        "ck_contracts_periodicidad",
        "contracts",
        "periodicidad IS NULL OR periodicidad IN "
        "('mensual', 'trimestral', 'semestral', 'anual', 'unico')",
    )
    op.drop_column("contracts", "importe")


def downgrade() -> None:
    op.add_column("contracts", sa.Column("importe", _AMOUNT, nullable=True))
    op.drop_constraint("ck_contracts_periodicidad", "contracts", type_="check")
    op.drop_column("contracts", "iva_incluido")
    op.drop_column("contracts", "importe_anual")
    op.drop_column("contracts", "importe_total")
    op.drop_column("contracts", "periodicidad")
    op.drop_column("contracts", "importe_periodico")
    op.drop_column("contracts", "fecha_firma")
