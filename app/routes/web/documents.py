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
from app.schemas.document_panel import PanelListParams
from app.services import (
    doc_type_service,
    document_panel_service,
    document_upload_service,
    invoice_service,
    ticket_service,
)

logger = structlog.get_logger(__name__)


router = APIRouter(prefix="/documents", tags=["documents"])


async def _documents_panel_ctx(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    list_params: PanelListParams | None = None,
    upload_errors: list[dict[str, str]] | None = None,
    upload_notices: list[dict[str, str]] | None = None,
    just_uploaded_ids: list[str] | None = None,
) -> dict[str, object]:
    invoices = await invoice_service.list_invoices(db, tenant_id, limit=50)

    tickets = await ticket_service.list_tickets(db, tenant_id, limit=50)

    doc_types = await doc_type_service.list_active_doc_types(db)

    params = list_params or PanelListParams()

    active_codes = {dt.code for dt in doc_types}

    if params.doc_type_code is not None and params.doc_type_code not in active_codes:
        params = PanelListParams(
            doc_type_code=None,
            sort=params.sort,
            dir=params.dir,
        )

    merged = document_panel_service.merge_panel_rows(
        invoices,
        tickets,
        doc_types=doc_types,
    )

    documents = document_panel_service.apply_list_params(merged, params)

    total_count = len(merged)

    return {
        "documents": documents,
        "doc_types": doc_types,
        "panel_list": params,
        "panel_filtered_empty": total_count > 0 and len(documents) == 0,
        "upload_errors": upload_errors or [],
        "upload_notices": upload_notices or [],
        "just_uploaded_ids": just_uploaded_ids or [],
    }


@router.get("")
async def documents_index(
    request: Request,
    _user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
    doc_type_code: str | None = None,
    sort: str | None = None,
    dir: str | None = None,
) -> HTMLResponse:
    list_params = PanelListParams.from_query(
        doc_type_code=doc_type_code,
        sort=sort,
        dir=dir,
    )

    ctx = await _documents_panel_ctx(db, tenant.id, list_params=list_params)

    return render(
        request,
        full="pages/documents/index.html",
        partial="components/invoices_panel.html",
        ctx=ctx,
    )


@router.get("/rows")
async def documents_rows(
    request: Request,
    _user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
    doc_type_code: str | None = None,
    sort: str | None = None,
    dir: str | None = None,
) -> HTMLResponse:
    list_params = PanelListParams.from_query(
        doc_type_code=doc_type_code,
        sort=sort,
        dir=dir,
    )

    ctx = await _documents_panel_ctx(db, tenant.id, list_params=list_params)

    return render(
        request,
        full="pages/documents/index.html",
        partial="components/invoices_panel.html",
        ctx=ctx,
    )


@router.post("/upload")
async def upload_documents(
    request: Request,
    _user: CurrentUser,
    tenant: CurrentTenant,
    doc_type_code: Annotated[str | None, Form()] = None,
    files: Annotated[list[UploadFile] | None, File(description="Document files")] = None,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    named_files = [f for f in (files or []) if f.filename]

    if not named_files:
        ctx = await _documents_panel_ctx(
            db,
            tenant.id,
            upload_errors=[{"filename": "—", "error": "No se ha seleccionado ningún fichero."}],
        )

        return render(
            request,
            full="pages/documents/index.html",
            partial="components/invoices_panel.html",
            ctx=ctx,
        )

    if len(named_files) > 20:
        ctx = await _documents_panel_ctx(
            db,
            tenant.id,
            upload_errors=[{"filename": "—", "error": "Máximo 20 ficheros por subida."}],
        )

        return render(
            request,
            full="pages/documents/index.html",
            partial="components/invoices_panel.html",
            ctx=ctx,
        )

    try:
        user_doc_type = doc_type_service.require_doc_type_form_value(doc_type_code)

    except ValidationError:
        ctx = await _documents_panel_ctx(
            db,
            tenant.id,
            upload_errors=[
                {"filename": "—", "error": "Debes indicar el tipo de documento."},
            ],
        )

        return render(
            request,
            full="pages/documents/index.html",
            partial="components/invoices_panel.html",
            ctx=ctx,
        )

    created_document_ids: list[str] = []

    errors: list[dict[str, str]] = []

    for upload in named_files:
        display_name = upload.filename or "file"

        try:
            data = await upload.read()

            mime = validate_invoice_upload(display_name, data)

            result = await document_upload_service.ingest_uploaded_document(
                db,
                tenant_id=tenant.id,
                filename=display_name,
                file_bytes=data,
                mime_type=mime,
                doc_type=user_doc_type,
            )

            created_document_ids.append(str(result.record_id))

        except UploadValidationError as exc:
            errors.append(
                {"filename": display_name, "error": str(exc)},
            )

            logger.warning(
                "upload.rejected",
                filename=display_name,
                error=str(exc),
            )

        except RuntimeError as exc:
            errors.append(
                {
                    "filename": display_name,
                    "error": "No se pudo encolar el procesamiento. Inténtalo de nuevo.",
                },
            )

            logger.exception(
                "upload.enqueue_failed",
                filename=display_name,
                error=str(exc),
            )

    ctx = await _documents_panel_ctx(
        db,
        tenant.id,
        upload_errors=errors,
        just_uploaded_ids=created_document_ids,
    )

    return render(
        request,
        full="pages/documents/index.html",
        partial="components/invoices_panel.html",
        ctx=ctx,
    )
