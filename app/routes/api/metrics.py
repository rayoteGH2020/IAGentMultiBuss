"""Métricas internas del módulo 1 (facturas + LLM).

Endpoint protegido por header `X-Metrics-Token`; pensado para scraping interno o panel admin.
Usa una sesión Postgres sin tenant (no aplica RLS — debe ejecutarse con rol con bypass o
propietario de tabla).

Por qué este endpoint importa modelos directamente (excepción a Agents.md §3):
Las métricas son agregaciones cross-tenant que no encajan en ningún service de
dominio concreto. Moverlas a un service añadiría una capa sin beneficio real,
y el endpoint es de solo lectura sin lógica de negocio.
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

# AsyncSession solo se necesita como anotación de tipo; el import se pospone a
# tiempo de comprobación (TYPE_CHECKING=False en runtime) para evitar
# importar sqlalchemy innecesariamente en rutas que no la usan directamente.
if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/metrics", tags=["metrics"])


async def _require_metrics_token(
    x_metrics_token: Annotated[str, Header(alias="X-Metrics-Token")],
) -> None:
    expected = get_settings().metrics_token.get_secret_value()
    # Se comprueba primero que `expected` no esté vacío: si METRICS_TOKEN no
    # está configurado, se rechaza la petición (fail closed). Aceptar cualquier
    # token cuando el secreto no existe sería una brecha de seguridad.
    if not expected or x_metrics_token != expected:
        raise ForbiddenError("Invalid metrics token")


@router.get(
    "/module1",
    summary="Métricas internas del módulo 1 (últimos 7 días)",
    # La dependencia se declara aquí (no como parámetro del handler) porque no
    # necesita devolver ningún valor: solo valida o lanza ForbiddenError.
    # FastAPI la evalúa antes de ejecutar el handler, por lo que si el token
    # es inválido, el handler nunca llega a correr.
    dependencies=[Depends(_require_metrics_token)],
)
async def module1_metrics(
    # get_db_no_tenant abre una sesión sin ejecutar `SET LOCAL app.current_tenant`,
    # por lo que RLS no filtra filas por tenant. Es necesario aquí porque las
    # métricas son agregaciones globales de todos los tenants. La sesión debe
    # ejecutarse con un rol que tenga BYPASSRLS o sea dueño de las tablas.
    db: AsyncSession = Depends(get_db_no_tenant),
) -> dict[str, Any]:
    # Ventana móvil de 7 días: suficiente para dashboards operacionales sin
    # cargar demasiados datos; UTC explícito para evitar ambigüedades de zona
    # horaria entre la app y Postgres.
    since = datetime.now(tz=UTC) - timedelta(days=7)

    # Consulta 1: distribución de facturas por estado (pending, processing,
    # ready, failed, reviewed). GROUP BY en BD es más eficiente que cargar
    # todas las filas y agrupar en Python.
    inv_rows = (
        await db.execute(
            select(Invoice.status, func.count())
            .where(Invoice.created_at >= since)
            .group_by(Invoice.status),
        )
    ).all()

    # `percentile_disc` es un agregado de conjunto ordenado de Postgres:
    # calcula el percentil exacto sin cargar todos los valores en memoria de la
    # app. p50 (mediana) y p95 son los umbrales objetivo de arquitectura.md §6.
    latency_p50 = func.percentile_disc(0.5).within_group(LLMCall.latency_ms.asc()).label("p50")
    latency_p95 = func.percentile_disc(0.95).within_group(LLMCall.latency_ms.asc()).label("p95")

    # Consulta 2: métricas de las llamadas LLM de tipo "extraction" en la
    # misma ventana temporal. Se filtra por task="extraction" para aislar solo
    # las llamadas del módulo 1 (no chat ni sql).
    llm_row = (
        await db.execute(
            select(
                func.count().label("calls"),
                # coalesce(..., 0): si no hay llamadas en el período, AVG y SUM
                # devuelven NULL; coalesce lo convierte en 0 para que el JSON
                # resultante siempre tenga un número, no None.
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
        # p50 y p95 pueden ser None si no hay llamadas; se preserva None en el
        # JSON (no se convierte a 0) para que el consumidor distinga "sin datos"
        # de "latencia cero".
        "latency_p50_ms": int(llm_row.p50) if llm_row.p50 is not None else None,
        "latency_p95_ms": int(llm_row.p95) if llm_row.p95 is not None else None,
        "total_cost_eur": float(llm_row.cost or 0),
    }
    # Se loguea el payload completo para tener un rastro de cuándo se consultó
    # y con qué valores, útil para auditar si los dashboards muestran anomalías.
    logger.info("metrics.module1", **payload)
    return payload
