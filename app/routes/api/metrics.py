"""Métricas internas del módulo 1 (facturas + LLM).

Endpoint protegido por header `X-Metrics-Token`; pensado para scraping interno o panel admin.
Usa una sesión Postgres sin tenant (no aplica RLS — debe ejecutarse con rol con bypass o
propietario de tabla).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header

from app.config import get_settings
from app.core.errors import ForbiddenError
from app.deps import get_db_no_tenant
from app.services import metrics_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/metrics", tags=["metrics"])


async def _require_metrics_token(
    x_metrics_token: Annotated[str, Header(alias="X-Metrics-Token")],
) -> None:
    expected = get_settings().metrics_token.get_secret_value()
    if not expected or x_metrics_token != expected:
        raise ForbiddenError("Invalid metrics token")


@router.get(
    "/module1",
    summary="Métricas internas del módulo 1 (últimos 7 días)",
    dependencies=[Depends(_require_metrics_token)],
)
async def module1_metrics(
    db: AsyncSession = Depends(get_db_no_tenant),
) -> dict[str, Any]:
    payload = await metrics_service.get_module1_metrics(db)
    logger.info("metrics.module1", **payload)
    return payload
