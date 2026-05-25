"""Polling HTMX sobre estado de jobs (facturas procesadas por ARQ).

El patrón de polling funciona así (arquitectura.md §10):
1. El endpoint /invoices/upload devuelve filas HTML con `hx-trigger="every 2s"`
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
from app.services import invoice_service, ticket_service

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
    logger.debug(
        "jobs.invoice_status",
        invoice_id=str(invoice_id),
        tenant_id=str(tenant.id),
        status=invoice.status.value,
    )
    # full y partial apuntan al mismo componente porque este endpoint solo
    # devuelve una fila; nunca se visita directamente como página completa.
    # Se mantiene la firma de render() por consistencia con el resto de routes
    # y por si en el futuro se añade una vista de detalle de job en página propia.
    # just_uploaded_ids vacío: el resaltado visual solo aplica justo después de
    # subir; en el polling posterior la fila ya no necesita destacarse.
    return render(
        request,
        full="components/invoice_row.html",
        partial="components/invoice_row.html",
        ctx={
            "invoice": invoice,
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
    logger.debug(
        "jobs.ticket_status",
        ticket_id=str(ticket_id),
        tenant_id=str(tenant.id),
        status=ticket.status.value,
    )
    return render(
        request,
        full="components/ticket_row.html",
        partial="components/ticket_row.html",
        ctx={
            "ticket": ticket,
            "just_uploaded_ticket_ids": [],
        },
    )
