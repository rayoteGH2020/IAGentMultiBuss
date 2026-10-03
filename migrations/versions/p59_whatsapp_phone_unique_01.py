"""Unique active WhatsApp phone_number_id (cross-tenant routing safety).

Revision ID: p59_wa_phone_unique_01
Revises: p58_drop_doc_types_dup_idx
Create Date: 2026-07-27

El webhook de WhatsApp resuelve el tenant por phone_number_id. Un índice no
único + LIMIT 1 permitía enrutar mensajes al tenant equivocado si SADM
duplicaba el ID. Este cambio:

1. Desactiva (status=revoked) duplicados activos, conservando el más reciente.
2. Sustituye el índice simple por un UNIQUE parcial sobre filas WhatsApp activas.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p59_wa_phone_unique_01"
down_revision: str | None = "p58_drop_doc_types_dup_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UNIQUE_INDEX = "uq_channel_integrations_wa_phone_active"
_OLD_INDEX = "ix_channel_integrations_phone_number_id"


def upgrade() -> None:
    # Conservar la fila más reciente por phone_number_id; el resto pasa a revoked
    # para poder crear el unique sin fallo. Credenciales se anulan por seguridad.
    op.execute(
        text(
            """
            WITH ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY phone_number_id
                           ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id
                       ) AS rn
                FROM channel_integrations
                WHERE channel = 'whatsapp'
                  AND status = 'active'
                  AND phone_number_id IS NOT NULL
            )
            UPDATE channel_integrations AS ci
            SET status = 'revoked',
                api_token_enc = NULL,
                webhook_secret_enc = NULL,
                updated_at = NOW()
            FROM ranked
            WHERE ci.id = ranked.id
              AND ranked.rn > 1;
            """
        )
    )
    op.execute(text(f"DROP INDEX IF EXISTS {_OLD_INDEX};"))
    op.execute(
        text(
            f"""
            CREATE UNIQUE INDEX {_UNIQUE_INDEX}
            ON channel_integrations (phone_number_id)
            WHERE status = 'active'
              AND phone_number_id IS NOT NULL
              AND channel = 'whatsapp';
            """
        )
    )


def downgrade() -> None:
    op.execute(text(f"DROP INDEX IF EXISTS {_UNIQUE_INDEX};"))
    op.execute(
        text(
            f"""
            CREATE INDEX {_OLD_INDEX}
            ON channel_integrations (phone_number_id);
            """
        )
    )
