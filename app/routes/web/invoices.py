from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.core.templating import render
from app.core.uploads import UploadValidationError, validate_invoice_upload
from app.deps import CurrentTenant, CurrentUser, get_db
from app.jobs.queue import enqueue_invoice_processing
from app.services import invoice_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get("")
async def invoices_index(
    request: Request,
    _user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    invoices = await invoice_service.list_invoices(db, tenant.id)
    return render(
        request,
        full="pages/invoices/index.html",
        partial="components/invoices_panel.html",
        ctx={
            "invoices": invoices,
            "upload_errors": [],
            "just_uploaded_ids": [],
        },
    )


@router.get("/rows")
async def invoices_rows(
    request: Request,
    _user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    invoices = await invoice_service.list_invoices(db, tenant.id, limit=50)
    return render(
        request,
        full="pages/invoices/index.html",
        partial="components/invoices_panel.html",
        ctx={
            "invoices": invoices,
            "upload_errors": [],
            "just_uploaded_ids": [],
        },
    )


@router.post("/upload")
async def upload_invoices(
    request: Request,
    _user: CurrentUser,
    tenant: CurrentTenant,
    files: Annotated[list[UploadFile], File(description="Invoice files")],
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    if not files:
        raise ValidationError("No files provided")
    if len(files) > 20:
        raise ValidationError("Max 20 files per upload")

    created = []
    errors: list[dict[str, str]] = []
    for upload in files:
        try:
            data = await upload.read()
            mime = validate_invoice_upload(upload.filename or "file", data)
            invoice = await invoice_service.create_invoice_from_upload(
                db,
                tenant_id=tenant.id,
                filename=upload.filename or "file",
                file_bytes=data,
                mime_type=mime,
            )
            await db.flush()
            await enqueue_invoice_processing(invoice.id, tenant.id)
            created.append(invoice)
        except UploadValidationError as exc:
            errors.append(
                {"filename": upload.filename or "file", "error": str(exc)},
            )
            logger.warning(
                "upload.rejected",
                filename=upload.filename,
                error=str(exc),
            )

    invoices = await invoice_service.list_invoices(db, tenant.id, limit=50)
    return render(
        request,
        full="pages/invoices/index.html",
        partial="components/invoices_panel.html",
        ctx={
            "invoices": invoices,
            "upload_errors": errors,
            "just_uploaded_ids": [str(i.id) for i in created],
        },
    )
