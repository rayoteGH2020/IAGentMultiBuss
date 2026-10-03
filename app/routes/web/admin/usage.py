"""SADM — consumo por organización (documentos, tokens y coste LLM)."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import render
from app.deps import SuperAdmin, get_db_no_tenant
from app.services import usage_service

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/sadm/usage", tags=["sadm"])


@router.get("", response_class=HTMLResponse)
async def tenant_usage(
    request: Request,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    period = usage_service.current_period()
    usage = await usage_service.get_tenant_usage(db, period=period)
    return render(
        request,
        full="pages/sadm/usage/index.html",
        partial="pages/sadm/usage/_table.html",
        ctx={"usage": usage, "period": period},
    )
