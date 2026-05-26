"""Polling HTMX sobre estado de jobs (facturas procesadas por ARQ).

El patrón de polling funciona así (arquitectura.md §10):
1. El endpoint /documents/upload devuelve filas HTML con `hx-trigger="every 2s"`
   apuntando a este endpoint mientras el invoice esté en estado pending/processing.
2. HTMX llama a este endpoint cada 2 segundos y reemplaza la fila con el HTML
   devuelto (hx-swap="outerHTML" sobre la fila correspondiente).
3. Cuando el invoice pasa a ready o failed, el template de la fila ya no incluye
   el atributo `hx-trigger`, por lo que el polling se detiene solo, sin
   necesidad de ninguna coordinación adicional en el servidor.
"""

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import render
from app.deps import CurrentTenant, CurrentUser, get_db
from app.services import (
    document_panel_service,
    invoice_service,
    knowledge_document_service,
    ticket_service,
)

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
    # Se pasa tenant.id al servicio para que la query incluya el filtro de
    # tenant, añadiendo una capa de defensa además de RLS: un usuario no podría
    # conocer el estado de la factura de otro tenant aunque adivinase su UUID.
    invoice = await invoice_service.get_invoice(db, tenant.id, invoice_id)
    document = document_panel_service.row_from_invoice(invoice)
    logger.debug(
        "jobs.invoice_status",
        invoice_id=str(invoice_id),
        tenant_id=str(tenant.id),
        status=document.status,
    )
    return render(
        request,
        full="components/document_row.html",
        partial="components/document_row.html",
        ctx={
            "document": document,
            "just_uploaded_ids": [],
        },
    )


@router.get("/ticket/{ticket_id}/status")
async def ticket_job_status_row(
    request: Request,
    ticket_id: UUID,
    _user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    ticket = await ticket_service.get_ticket(db, tenant.id, ticket_id)
    document = document_panel_service.row_from_ticket(ticket)
    logger.debug(
        "jobs.ticket_status",
        ticket_id=str(ticket_id),
        tenant_id=str(tenant.id),
        status=document.status,
    )
    return render(
        request,
        full="components/document_row.html",
        partial="components/document_row.html",
        ctx={
            "document": document,
            "just_uploaded_ids": [],
        },
    )


@router.get("/knowledge/{document_id}/status")
async def knowledge_job_status_row(
    request: Request,
    document_id: UUID,
    _user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    doc = await knowledge_document_service.get_document(
        db,
        tenant_id=tenant.id,
        document_id=document_id,
        include_download_url=False,
    )
    logger.debug(
        "jobs.knowledge_status",
        document_id=str(document_id),
        tenant_id=str(tenant.id),
        status=doc.status,
    )
    return render(
        request,
        full="components/knowledge_row.html",
        partial="components/knowledge_row.html",
        ctx={"document": doc},
    )
