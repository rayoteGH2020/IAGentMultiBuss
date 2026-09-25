"""SADM — traza completa de conversaciones /chat."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import render
from app.deps import SuperAdmin, get_db_no_tenant
from app.services import chat_trace_service

router = APIRouter(prefix="/sadm/chat-traces", tags=["sadm"])


@router.get("", response_class=HTMLResponse)
async def chat_traces_list(
    request: Request,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
    tenant_id: UUID | None = Query(default=None),
    include_hidden: bool = Query(default=True),
) -> HTMLResponse:
    threads = await chat_trace_service.list_threads(
        db,
        tenant_id=tenant_id,
        include_hidden=include_hidden,
    )
    return render(
        request,
        full="pages/sadm/chat_traces/index.html",
        partial="pages/sadm/chat_traces/_list.html",
        ctx={
            "threads": threads,
            "tenant_id": tenant_id,
            "include_hidden": include_hidden,
        },
    )


@router.get("/{thread_id}", response_class=HTMLResponse)
async def chat_trace_detail(
    request: Request,
    thread_id: UUID,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    trace = await chat_trace_service.get_thread_trace(db, thread_id=thread_id)
    return render(
        request,
        full="pages/sadm/chat_traces/detail.html",
        partial="pages/sadm/chat_traces/_detail.html",
        ctx={"trace": trace},
    )
