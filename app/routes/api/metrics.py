"""Métricas internas del módulo 1 (facturas + LLM).

Endpoint protegido por header `X-Metrics-Token`; pensado para scraping interno o panel admin.
Usa una sesión Postgres sin tenant (no aplica RLS — debe ejecutarse con rol con bypass o
propietario de tabla).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header
from sqlalchemy import func, select

from app.config import get_settings
from app.core.errors import ForbiddenError
from app.deps import get_db_no_tenant
from app.models import Invoice, LLMCall

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
    since = datetime.now(tz=UTC) - timedelta(days=7)

    inv_rows = (
        await db.execute(
            select(Invoice.status, func.count())
            .where(Invoice.created_at >= since)
            .group_by(Invoice.status),
        )
    ).all()

    latency_p50 = func.percentile_disc(0.5).within_group(LLMCall.latency_ms.asc()).label("p50")
    latency_p95 = func.percentile_disc(0.95).within_group(LLMCall.latency_ms.asc()).label("p95")
    llm_row = (
        await db.execute(
            select(
                func.count().label("calls"),
                func.coalesce(func.avg(LLMCall.latency_ms), 0).label("avg_ms"),
                latency_p50,
                latency_p95,
                func.coalesce(func.sum(LLMCall.cost_eur), 0).label("cost"),
            ).where(
                LLMCall.task == "extraction",
                LLMCall.created_at >= since,
            ),
        )
    ).one()

    payload = {
        "since": since.isoformat(),
        "invoices_by_status": {str(status.value): int(count) for status, count in inv_rows},
        "extraction_calls": int(llm_row.calls or 0),
        "latency_avg_ms": float(llm_row.avg_ms or 0),
        "latency_p50_ms": int(llm_row.p50) if llm_row.p50 is not None else None,
        "latency_p95_ms": int(llm_row.p95) if llm_row.p95 is not None else None,
        "total_cost_eur": float(llm_row.cost or 0),
    }
    logger.info("metrics.module1", **payload)
    return payload
