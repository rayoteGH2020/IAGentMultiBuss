"""SADM — coste y volumen de chat documental por tenant."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import render
from app.deps import SuperAdmin, get_db_no_tenant
from app.services import chat_usage_service
from app.services.chat_usage_service import ChatUsageSortDir, ChatUsageView

router = APIRouter(prefix="/sadm/chat-usage", tags=["sadm"])

_VIEWS: frozenset[str] = frozenset({"day", "week", "month"})
_SORTS: frozenset[str] = frozenset({"asc", "desc"})


def _parse_view(raw: str) -> ChatUsageView:
    return raw if raw in _VIEWS else "month"  # type: ignore[return-value]


def _parse_sort(raw: str) -> ChatUsageSortDir:
    return raw if raw in _SORTS else "desc"  # type: ignore[return-value]


@router.get("", response_class=HTMLResponse)
async def chat_usage_tenants(
    request: Request,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    tenants = await chat_usage_service.list_active_tenants(db)
    return render(
        request,
        full="pages/sadm/chat_usage/index.html",
        partial="pages/sadm/chat_usage/_tenants.html",
        ctx={
            "tenants": tenants,
            "tenant_options": chat_usage_service.tenant_picker_options(tenants),
            "selected_tenant_id": None,
        },
    )


@router.get("/{tenant_id}", response_class=HTMLResponse)
async def chat_usage_tenant_detail(
    request: Request,
    tenant_id: UUID,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
    view: str = Query(default="month"),
    anchor: date | None = Query(default=None),
    sort: str = Query(default="desc"),
) -> HTMLResponse:
    tenants = await chat_usage_service.list_active_tenants(db)
    report = await chat_usage_service.get_tenant_chat_usage(
        db,
        tenant_id=tenant_id,
        view=_parse_view(view),
        anchor=anchor,
        sort=_parse_sort(sort),
    )
    return render(
        request,
        full="pages/sadm/chat_usage/detail.html",
        partial="pages/sadm/chat_usage/_report.html",
        ctx={
            "report": report,
            "tenants": tenants,
            "tenant_options": chat_usage_service.tenant_picker_options(tenants),
            "selected_tenant_id": tenant_id,
        },
    )
