"""Polling HTMX sobre estado de jobs (facturas procesadas por ARQ)."""

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import render
from app.deps import CurrentTenant, CurrentUser, get_db
from app.services import invoice_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/invoice/{invoice_id}/status")
async def invoice_job_status_row(
    request: Request,
    invoice_id: UUID,
    _user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    invoice = await invoice_service.get_invoice(db, tenant.id, invoice_id)
    logger.debug(
        "jobs.invoice_status",
        invoice_id=str(invoice_id),
        tenant_id=str(tenant.id),
        status=invoice.status.value,
    )
    return render(
        request,
        full="components/invoice_row.html",
        partial="components/invoice_row.html",
        ctx={
            "invoice": invoice,
            "just_uploaded_ids": [],
        },
    )
