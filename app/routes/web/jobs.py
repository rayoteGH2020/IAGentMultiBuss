"""Estado de jobs para filas de documentos / knowledge.

El panel de documentos hace polling HTMX nativo
(``hx-trigger="every 2s"`` en el ``<tbody>``, como knowledge), con
``hx-boost/push-url/history`` desactivados en la fila y
``HX-Push-Url: false`` en la respuesta.

Mientras el documento sigue busy se intercambia solo la fila. Al terminar
(ready/failed/…), se responde el panel completo con ``HX-Retarget`` para
quitar «Recién subidos» y dejar el listado unificado «Documentos».
"""

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import render
from app.deps import CurrentTenant, CurrentUser, get_db, require_feature
from app.schemas.document_panel import PanelDocumentRow
from app.services import (
    contract_service,
    document_panel_service,
    document_processing_service,
    insurance_service,
    invoice_service,
    knowledge_document_service,
    ticket_service,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

_RequireDocumentsFeature = Depends(require_feature("documents"))
_RequireKnowledgeFeature = Depends(require_feature("knowledge"))


def _status_row_response(
    request: Request, *, template: str, ctx: dict[str, object]
) -> HTMLResponse:
    response = render(
        request,
        full=template,
        partial=template,
        ctx=ctx,
    )
    response.headers["HX-Push-Url"] = "false"
    response.headers["Cache-Control"] = "no-store"
    return response


async def _document_status_response(
    request: Request,
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document: PanelDocumentRow,
) -> HTMLResponse:
    """Fila mientras busy; panel completo (sin recién subidos) al terminar."""
    if document_panel_service.is_document_status_busy(document.status):
        return _status_row_response(
            request,
            template="components/document_row.html",
            ctx={"document": document, "just_uploaded_ids": []},
        )

    ctx = await document_panel_service.build_invoices_panel_ctx(
        db,
        tenant_id,
        just_uploaded_ids=[],
    )
    response = render(
        request,
        full="components/invoices_panel.html",
        partial="components/invoices_panel.html",
        ctx=ctx,
    )
    response.headers["HX-Push-Url"] = "false"
    response.headers["Cache-Control"] = "no-store"
    # El poll apunta al <tbody>; al terminar redirigimos el swap al panel entero.
    response.headers["HX-Retarget"] = "#invoices-table-container"
    response.headers["HX-Reswap"] = "outerHTML"
    return response


@router.get("/invoice/{invoice_id}/status", dependencies=[_RequireDocumentsFeature])
async def invoice_job_status_row(
    request: Request,
    invoice_id: UUID,
    _user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    await document_processing_service.abandon_stale_processing(
        db,
        tenant_id=tenant.id,
        document_kind="invoice",
        document_id=invoice_id,
    )
    invoice = await invoice_service.get_invoice(db, tenant.id, invoice_id)
    document = document_panel_service.row_from_invoice(invoice)
    logger.debug(
        "jobs.invoice_status",
        invoice_id=str(invoice_id),
        tenant_id=str(tenant.id),
        status=document.status,
    )
    return await _document_status_response(
        request,
        db,
        tenant_id=tenant.id,
        document=document,
    )


@router.get("/ticket/{ticket_id}/status", dependencies=[_RequireDocumentsFeature])
async def ticket_job_status_row(
    request: Request,
    ticket_id: UUID,
    _user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    await document_processing_service.abandon_stale_processing(
        db,
        tenant_id=tenant.id,
        document_kind="ticket",
        document_id=ticket_id,
    )
    ticket = await ticket_service.get_ticket(db, tenant.id, ticket_id)
    document = document_panel_service.row_from_ticket(ticket)
    logger.debug(
        "jobs.ticket_status",
        ticket_id=str(ticket_id),
        tenant_id=str(tenant.id),
        status=document.status,
    )
    return await _document_status_response(
        request,
        db,
        tenant_id=tenant.id,
        document=document,
    )


@router.get("/contract/{contract_id}/status", dependencies=[_RequireDocumentsFeature])
async def contract_job_status_row(
    request: Request,
    contract_id: UUID,
    _user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    await document_processing_service.abandon_stale_processing(
        db,
        tenant_id=tenant.id,
        document_kind="contract",
        document_id=contract_id,
    )
    contract = await contract_service.get_contract(db, tenant.id, contract_id)
    document = document_panel_service.row_from_contract(contract)
    logger.debug(
        "jobs.contract_status",
        contract_id=str(contract_id),
        tenant_id=str(tenant.id),
        status=document.status,
    )
    return await _document_status_response(
        request,
        db,
        tenant_id=tenant.id,
        document=document,
    )


@router.get("/insurance/{insurance_id}/status", dependencies=[_RequireDocumentsFeature])
async def insurance_job_status_row(
    request: Request,
    insurance_id: UUID,
    _user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    await document_processing_service.abandon_stale_processing(
        db,
        tenant_id=tenant.id,
        document_kind="insurance",
        document_id=insurance_id,
    )
    insurance = await insurance_service.get_insurance(db, tenant.id, insurance_id)
    document = document_panel_service.row_from_insurance(insurance)
    logger.debug(
        "jobs.insurance_status",
        insurance_id=str(insurance_id),
        tenant_id=str(tenant.id),
        status=document.status,
    )
    return await _document_status_response(
        request,
        db,
        tenant_id=tenant.id,
        document=document,
    )


@router.get("/knowledge/{document_id}/status", dependencies=[_RequireKnowledgeFeature])
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
    return _status_row_response(
        request,
        template="components/knowledge_row.html",
        ctx={"document": doc},
    )
