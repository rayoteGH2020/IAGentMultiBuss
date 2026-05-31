"""SADM dashboard — página de inicio de la consola de superadmin (Paso 50)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.templating import render
from app.deps import SuperAdmin

router = APIRouter(prefix="/sadm", tags=["sadm"])


@router.get("", response_class=HTMLResponse)
async def sadm_dashboard(request: Request, _admin: SuperAdmin) -> HTMLResponse:
    return render(
        request,
        full="pages/sadm/dashboard.html",
        partial="pages/sadm/dashboard.html",
        ctx={},
    )
