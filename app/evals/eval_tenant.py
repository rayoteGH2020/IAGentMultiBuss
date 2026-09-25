"""Tenant de evals para runners que persisten llm_calls (FK a tenants)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.db import session_scope
from app.models import Tenant

# UUID fijo: reutilizable en CI y local sin pasar argumento al runner.
EVAL_TENANT_ID = uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-111111111111")


async def ensure_eval_tenant(requested: uuid.UUID | None = None) -> uuid.UUID:
    """Devuelve un tenant_id válido en BD para evals de extracción.

    Si ``requested`` está definido, comprueba que exista en ``tenants``.
    Si no, crea (idempotente) un tenant dedicado a evals con ``EVAL_TENANT_ID``.
    """
    if requested is not None:
        async with session_scope() as db:
            row = await db.execute(select(Tenant.id).where(Tenant.id == requested).limit(1))
            if row.scalar_one_or_none() is None:
                msg = (
                    f"No existe tenant con id={requested}. "
                    "Usa un UUID de la tabla tenants o omite el argumento para "
                    "usar el tenant de evals auto-provisionado."
                )
                raise SystemExit(msg)
        return requested

    async with session_scope() as db:
        row = await db.execute(select(Tenant.id).where(Tenant.id == EVAL_TENANT_ID).limit(1))
        if row.scalar_one_or_none() is None:
            db.add(
                Tenant(
                    id=EVAL_TENANT_ID,
                    name="Invoice extraction eval",
                    clerk_org_id=f"eval-extraction-{EVAL_TENANT_ID.hex[:12]}",
                ),
            )
    return EVAL_TENANT_ID
