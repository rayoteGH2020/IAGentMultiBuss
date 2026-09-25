"""Agregación de coste/uso de chat documental para la consola SADM.

Ventanas en zona de visualización de la app (por defecto Europe/Madrid):

- ``day``: últimos 7 días, una fila por día; Anterior/Siguiente desplaza 7 días.
- ``week``: últimas 6 semanas (L-D), una fila agregada por semana; Anterior/Siguiente
  desplaza la ventana 6 semanas.
- ``month``: últimos 6 meses, una fila agregada por mes; Anterior/Siguiente desplaza
  6 meses.

Coste/tokens/latencia desde ``llm_calls.task = 'chat'``; recuento de chats = hilos
distintos con mensaje de usuario en el periodo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.core.datetime_display import display_today, resolve_display_timezone
from app.core.errors import NotFoundError
from app.models import ChatMessage, ChatMessageRole, LLMCall, Tenant
from app.services.document_override_service import enable_superadmin_lookup

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

ChatUsageView = Literal["day", "week", "month"]
ChatUsageSortDir = Literal["asc", "desc"]

# Tamaño de ventana visible y paso de Anterior/Siguiente.
DAY_WINDOW = 7
PERIOD_WINDOW = 6  # semanas o meses


@dataclass(frozen=True, slots=True)
class ChatUsageTenantItem:
    """Tenant visible en el listado inicial."""

    id: UUID
    name: str
    plan: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ChatUsageDayRow:
    """Métricas de chat agregadas para un bucket (día, semana o mes)."""

    period_date: date
    period_label: str
    chat_count: int
    llm_calls: int
    input_tokens: int
    output_tokens: int
    cost_eur: Decimal
    avg_latency_ms: float


@dataclass(frozen=True, slots=True)
class ChatUsagePeriod:
    """Ventana visible y controles de navegación."""

    view: ChatUsageView
    anchor: date
    start: date
    end_exclusive: date
    label: str
    can_go_back: bool
    can_go_forward: bool
    prev_anchor: date
    next_anchor: date | None


@dataclass(frozen=True, slots=True)
class ChatUsageReport:
    """Detalle de uso de chat de un tenant en una ventana."""

    tenant_id: UUID
    tenant_name: str
    period: ChatUsagePeriod
    rows: list[ChatUsageDayRow]
    sort: ChatUsageSortDir
    totals: ChatUsageDayRow


@dataclass(frozen=True, slots=True)
class _UsageBucket:
    start: date
    end_exclusive: date
    label: str


def _tz() -> ZoneInfo:
    return resolve_display_timezone()


def _today() -> date:
    return display_today()


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _month_start(day: date) -> date:
    return date(day.year, day.month, 1)


def _add_months(day: date, delta: int) -> date:
    month_index = day.year * 12 + (day.month - 1) + delta
    year, month0 = divmod(month_index, 12)
    return date(year, month0 + 1, 1)


def resolve_period(*, view: ChatUsageView, anchor: date | None = None) -> ChatUsagePeriod:
    """Calcula la ventana [start, end) y si se puede avanzar/retroceder."""
    today = _today()
    raw = anchor or today

    if view == "day":
        end = min(raw, today)
        start = end - timedelta(days=DAY_WINDOW - 1)
        end_exclusive = end + timedelta(days=1)
        prev_anchor = end - timedelta(days=DAY_WINDOW)
        next_candidate = end + timedelta(days=DAY_WINDOW)
        if next_candidate > today:
            next_candidate = today
        next_anchor = next_candidate if next_candidate > end else None
        return ChatUsagePeriod(
            view=view,
            anchor=end,
            start=start,
            end_exclusive=end_exclusive,
            label=f"{start.isoformat()} → {end.isoformat()} ({DAY_WINDOW} días)",
            can_go_back=True,
            can_go_forward=next_anchor is not None,
            prev_anchor=prev_anchor,
            next_anchor=next_anchor,
        )

    if view == "week":
        current_end = _monday_of(today)
        end = _monday_of(min(raw, today))
        if end > current_end:
            end = current_end
        start = end - timedelta(weeks=PERIOD_WINDOW - 1)
        end_exclusive = end + timedelta(days=7)
        prev_anchor = end - timedelta(weeks=PERIOD_WINDOW)
        next_candidate = end + timedelta(weeks=PERIOD_WINDOW)
        if next_candidate > current_end:
            next_candidate = current_end
        next_anchor = next_candidate if next_candidate > end else None
        last_day = end_exclusive - timedelta(days=1)
        return ChatUsagePeriod(
            view=view,
            anchor=end,
            start=start,
            end_exclusive=end_exclusive,
            label=f"{start.isoformat()} → {last_day.isoformat()} ({PERIOD_WINDOW} semanas)",
            can_go_back=True,
            can_go_forward=next_anchor is not None,
            prev_anchor=prev_anchor,
            next_anchor=next_anchor,
        )

    current_end = _month_start(today)
    end = _month_start(min(raw, today))
    if end > current_end:
        end = current_end
    start = _add_months(end, -(PERIOD_WINDOW - 1))
    end_exclusive = _add_months(end, 1)
    prev_anchor = _add_months(end, -PERIOD_WINDOW)
    next_candidate = _add_months(end, PERIOD_WINDOW)
    if next_candidate > current_end:
        next_candidate = current_end
    next_anchor = next_candidate if next_candidate > end else None
    return ChatUsagePeriod(
        view=view,
        anchor=end,
        start=start,
        end_exclusive=end_exclusive,
        label=(
            f"{start.year}-{start.month:02d} → {end.year}-{end.month:02d} ({PERIOD_WINDOW} meses)"
        ),
        can_go_back=True,
        can_go_forward=next_anchor is not None,
        prev_anchor=prev_anchor,
        next_anchor=next_anchor,
    )


def _iter_buckets(period: ChatUsagePeriod) -> list[_UsageBucket]:
    """Buckets (día / semana / mes) que forman las filas del informe."""
    buckets: list[_UsageBucket] = []
    cursor = period.start

    if period.view == "day":
        while cursor < period.end_exclusive:
            end = cursor + timedelta(days=1)
            buckets.append(_UsageBucket(start=cursor, end_exclusive=end, label=cursor.isoformat()))
            cursor = end
        return buckets

    if period.view == "week":
        while cursor < period.end_exclusive:
            end = cursor + timedelta(days=7)
            label = f"{cursor.isoformat()} → {(end - timedelta(days=1)).isoformat()}"
            buckets.append(_UsageBucket(start=cursor, end_exclusive=end, label=label))
            cursor = end
        return buckets

    while cursor < period.end_exclusive:
        end = _add_months(cursor, 1)
        label = f"{cursor.year}-{cursor.month:02d}"
        buckets.append(_UsageBucket(start=cursor, end_exclusive=end, label=label))
        cursor = end
    return buckets


def _as_utc_bounds(start: date, end_exclusive: date) -> tuple[datetime, datetime]:
    tz = _tz()
    start_local = datetime(start.year, start.month, start.day, tzinfo=tz)
    end_local = datetime(end_exclusive.year, end_exclusive.month, end_exclusive.day, tzinfo=tz)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _empty_row(*, period_date: date, period_label: str) -> ChatUsageDayRow:
    return ChatUsageDayRow(
        period_date=period_date,
        period_label=period_label,
        chat_count=0,
        llm_calls=0,
        input_tokens=0,
        output_tokens=0,
        cost_eur=Decimal("0"),
        avg_latency_ms=0.0,
    )


def _totals(
    rows: list[ChatUsageDayRow],
    *,
    period_date: date,
    period_label: str,
) -> ChatUsageDayRow:
    total_calls = sum(r.llm_calls for r in rows)
    if total_calls > 0:
        avg_latency = sum(r.avg_latency_ms * r.llm_calls for r in rows) / total_calls
    else:
        avg_latency = 0.0
    return ChatUsageDayRow(
        period_date=period_date,
        period_label=period_label,
        chat_count=sum(r.chat_count for r in rows),
        llm_calls=total_calls,
        input_tokens=sum(r.input_tokens for r in rows),
        output_tokens=sum(r.output_tokens for r in rows),
        cost_eur=sum((r.cost_eur for r in rows), Decimal("0")),
        avg_latency_ms=avg_latency,
    )


def _aggregate_bucket(
    bucket: _UsageBucket,
    *,
    llm_by_day: dict[date, Any],
    chats_by_day: dict[date, int],
    today: date,
) -> ChatUsageDayRow:
    """Suma métricas diarias dentro del bucket (hasta hoy inclusive)."""
    day_rows: list[ChatUsageDayRow] = []
    cursor = bucket.start
    while cursor < bucket.end_exclusive and cursor <= today:
        llm = llm_by_day.get(cursor)
        day_rows.append(
            ChatUsageDayRow(
                period_date=cursor,
                period_label=cursor.isoformat(),
                chat_count=chats_by_day.get(cursor, 0),
                llm_calls=int(llm.calls) if llm else 0,
                input_tokens=int(llm.input_tokens) if llm else 0,
                output_tokens=int(llm.output_tokens) if llm else 0,
                cost_eur=Decimal(str(llm.cost_eur)) if llm else Decimal("0"),
                avg_latency_ms=float(llm.avg_latency_ms) if llm else 0.0,
            )
        )
        cursor += timedelta(days=1)
    if not day_rows:
        return _empty_row(period_date=bucket.start, period_label=bucket.label)
    return _totals(day_rows, period_date=bucket.start, period_label=bucket.label)


async def list_active_tenants(db: AsyncSession) -> list[ChatUsageTenantItem]:
    """Todos los tenants (no hay soft-delete); orden alfabético."""
    rows = (
        (await db.execute(select(Tenant).order_by(Tenant.name.asc(), Tenant.created_at.desc())))
        .scalars()
        .all()
    )
    return [
        ChatUsageTenantItem(
            id=tenant.id,
            name=tenant.name,
            plan=tenant.plan,
            created_at=tenant.created_at,
        )
        for tenant in rows
    ]


def tenant_picker_options(tenants: list[ChatUsageTenantItem]) -> list[dict[str, str]]:
    """Opciones serializables para el combobox Alpine de selección de tenant."""
    return [{"id": str(tenant.id), "name": tenant.name, "plan": tenant.plan} for tenant in tenants]


async def get_tenant_chat_usage(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    view: ChatUsageView = "month",
    anchor: date | None = None,
    sort: ChatUsageSortDir = "desc",
) -> ChatUsageReport:
    """Métricas de chat del tenant agregadas por día / semana / mes."""
    await enable_superadmin_lookup(db)
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    if tenant is None:
        raise NotFoundError("Tenant not found")

    period = resolve_period(view=view, anchor=anchor)
    start_utc, end_utc = _as_utc_bounds(period.start, period.end_exclusive)
    tz_name = str(_tz())
    local_day = func.date(func.timezone(tz_name, LLMCall.created_at))

    llm_stmt = (
        select(
            local_day.label("period_date"),
            func.count(LLMCall.id).label("calls"),
            func.coalesce(func.sum(LLMCall.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(LLMCall.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(LLMCall.cost_eur), 0).label("cost_eur"),
            func.coalesce(func.avg(LLMCall.latency_ms), 0).label("avg_latency_ms"),
        )
        .where(
            LLMCall.tenant_id == tenant_id,
            LLMCall.task == "chat",
            LLMCall.created_at >= start_utc,
            LLMCall.created_at < end_utc,
        )
        .group_by(local_day)
    )
    llm_by_day = {
        row.period_date: row for row in (await db.execute(llm_stmt)).all() if row.period_date
    }

    msg_day = func.date(func.timezone(tz_name, ChatMessage.created_at))
    chat_stmt = (
        select(
            msg_day.label("period_date"),
            func.count(func.distinct(ChatMessage.thread_id)).label("chat_count"),
        )
        .where(
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.role == ChatMessageRole.user,
            ChatMessage.created_at >= start_utc,
            ChatMessage.created_at < end_utc,
        )
        .group_by(msg_day)
    )
    chats_by_day = {
        row.period_date: int(row.chat_count)
        for row in (await db.execute(chat_stmt)).all()
        if row.period_date
    }

    today = _today()
    rows = [
        _aggregate_bucket(
            bucket,
            llm_by_day=llm_by_day,
            chats_by_day=chats_by_day,
            today=today,
        )
        for bucket in _iter_buckets(period)
    ]
    rows.sort(key=lambda r: r.period_date, reverse=(sort == "desc"))

    return ChatUsageReport(
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        period=period,
        rows=rows,
        sort=sort,
        totals=_totals(
            rows,
            period_date=period.start,
            period_label="Total",
        ),
    )
