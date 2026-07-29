"""Agregaciones cross-tenant de métricas internas (módulo 1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from app.models import Invoice, LLMCall

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def get_module1_metrics(db: AsyncSession) -> dict[str, Any]:
    """Métricas de facturas y extracción LLM en ventana móvil de 7 días."""
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

    return {
        "since": since.isoformat(),
        "invoices_by_status": {str(status.value): int(count) for status, count in inv_rows},
        "extraction_calls": int(llm_row.calls or 0),
        "latency_avg_ms": float(llm_row.avg_ms or 0),
        "latency_p50_ms": int(llm_row.p50) if llm_row.p50 is not None else None,
        "latency_p95_ms": int(llm_row.p95) if llm_row.p95 is not None else None,
        "total_cost_eur": float(llm_row.cost or 0),
    }
