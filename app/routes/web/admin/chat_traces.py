"""SADM — traza completa de conversaciones /chat del tenant propio.

El superadmin solo ve las conversaciones de su propia organización: la sesión
lleva RLS de su tenant (``get_db``) y el servicio filtra por ``tenant_id``.
Las de otros tenants no son accesibles (ver p72 y AGENTS.md §7).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import render
from app.deps import CurrentUser, SuperAdmin, get_db
from app.routes.web.audit_context import audit_request_context
from app.services import chat_trace_service

router = APIRouter(prefix="/sadm/chat-traces", tags=["sadm"])


@router.get("", response_class=HTMLResponse)
async def chat_traces_list(
    request: Request,
    tenant: SuperAdmin,
    db: AsyncSession = Depends(get_db),
    include_hidden: bool = Query(default=True),
) -> HTMLResponse:
    threads = await chat_trace_service.list_threads(
        db,
        tenant_id=tenant.id,
        include_hidden=include_hidden,
    )
    return render(
        request,
        full="pages/sadm/chat_traces/index.html",
        partial="pages/sadm/chat_traces/_list.html",
        ctx={
            "threads": threads,
            "include_hidden": include_hidden,
        },
    )


@router.get("/{thread_id}", response_class=HTMLResponse)
async def chat_trace_detail(
    request: Request,
    thread_id: UUID,
    tenant: SuperAdmin,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    trace = await chat_trace_service.get_thread_trace(
        db,
        tenant_id=tenant.id,
        thread_id=thread_id,
        viewer_id=user.id,
        request_ctx=audit_request_context(request),
    )
    return render(
        request,
        full="pages/sadm/chat_traces/detail.html",
        partial="pages/sadm/chat_traces/_detail.html",
        ctx={"trace": trace},
    )
