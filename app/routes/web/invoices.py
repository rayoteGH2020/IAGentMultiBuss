from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.core.templating import render
from app.core.uploads import UploadValidationError, validate_invoice_upload
from app.deps import CurrentTenant, CurrentUser, get_db
from app.services import doc_type_service, document_upload_service, invoice_service, ticket_service

logger = structlog.get_logger(__name__)


router = APIRouter(prefix="/invoices", tags=["invoices"])


async def _invoices_panel_ctx(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    upload_errors: list[dict[str, str]] | None = None,
    upload_notices: list[dict[str, str]] | None = None,
    just_uploaded_ids: list[str] | None = None,
    just_uploaded_ticket_ids: list[str] | None = None,
) -> dict[str, object]:
    invoices = await invoice_service.list_invoices(db, tenant_id, limit=50)

    tickets = await ticket_service.list_tickets(db, tenant_id, limit=50)

    doc_types = await doc_type_service.list_active_doc_types(db)

    return {
        "invoices": invoices,
        "tickets": tickets,
        "doc_types": doc_types,
        "upload_errors": upload_errors or [],
        "upload_notices": upload_notices or [],
        "just_uploaded_ids": just_uploaded_ids or [],
        "just_uploaded_ticket_ids": just_uploaded_ticket_ids or [],
    }


@router.get("")
async def invoices_index(
    request: Request,
    _user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    ctx = await _invoices_panel_ctx(db, tenant.id)

    return render(
        request,
        full="pages/invoices/index.html",
        partial="components/invoices_panel.html",
        ctx=ctx,
    )


@router.get("/rows")
async def invoices_rows(
    request: Request,
    _user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    ctx = await _invoices_panel_ctx(db, tenant.id)

    return render(
        request,
        full="pages/invoices/index.html",
        partial="components/invoices_panel.html",
        ctx=ctx,
    )


@router.post("/upload")
async def upload_invoices(
    request: Request,
    _user: CurrentUser,
    tenant: CurrentTenant,
    files: Annotated[list[UploadFile], File(description="Document files")],
    doc_type_code: Annotated[str | None, Form()] = None,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    if not files:
        raise ValidationError("No files provided")

    if len(files) > 20:
        raise ValidationError("Max 20 files per upload")

    try:
        user_doc_type = doc_type_service.require_doc_type_form_value(doc_type_code)
    except ValidationError:
        ctx = await _invoices_panel_ctx(
            db,
            tenant.id,
            upload_errors=[
                {"filename": "—", "error": "Debes indicar el tipo de documento."},
            ],
        )
        return render(
            request,
            full="pages/invoices/index.html",
            partial="components/invoices_panel.html",
            ctx=ctx,
        )

    created_invoice_ids: list[str] = []

    created_ticket_ids: list[str] = []

    errors: list[dict[str, str]] = []

    for upload in files:
        try:
            data = await upload.read()

            mime = validate_invoice_upload(upload.filename or "file", data)

            result = await document_upload_service.ingest_uploaded_document(
                db,
                tenant_id=tenant.id,
                filename=upload.filename or "file",
                file_bytes=data,
                mime_type=mime,
                doc_type=user_doc_type,
            )

            if result.kind == "invoice" and result.invoice is not None:
                created_invoice_ids.append(str(result.invoice.id))

            elif result.kind == "ticket" and result.ticket is not None:
                created_ticket_ids.append(str(result.ticket.id))

        except UploadValidationError as exc:
            errors.append(
                {"filename": upload.filename or "file", "error": str(exc)},
            )

            logger.warning(
                "upload.rejected",
                filename=upload.filename,
                error=str(exc),
            )

    ctx = await _invoices_panel_ctx(
        db,
        tenant.id,
        upload_errors=errors,
        just_uploaded_ids=created_invoice_ids,
        just_uploaded_ticket_ids=created_ticket_ids,
    )

    return render(
        request,
        full="pages/invoices/index.html",
        partial="components/invoices_panel.html",
        ctx=ctx,
    )
