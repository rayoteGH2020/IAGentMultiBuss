from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RateLimitError, ValidationError
from app.core.templating import render
from app.core.uploads import (
    MAX_FILE_SIZE,
    MAX_FILES_PER_UPLOAD,
    UploadValidationError,
    read_upload_limited,
    validate_invoice_upload,
)
from app.deps import CurrentTenant, CurrentUser, RedisDep, get_db, require_feature
from app.schemas.document_panel import PanelListParams
from app.services import (
    contract_service,
    doc_type_service,
    document_delete_service,
    document_panel_service,
    document_processing_service,
    document_type_confirm_service,
    document_upload_service,
    entitlement_service,
    insurance_service,
    invoice_service,
    ticket_service,
)
from app.services.audit_service import AuditRequestContext

logger = structlog.get_logger(__name__)


router = APIRouter(
    prefix="/documents",
    tags=["documents"],
    dependencies=[Depends(require_feature("documents"))],
)


async def _documents_panel_ctx(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    list_params: PanelListParams | None = None,
    upload_errors: list[dict[str, str]] | None = None,
    upload_notices: list[dict[str, str]] | None = None,
    just_uploaded_ids: list[str] | None = None,
) -> dict[str, object]:
    return await document_panel_service.build_invoices_panel_ctx(
        db,
        tenant_id,
        list_params=list_params,
        upload_errors=upload_errors,
        upload_notices=upload_notices,
        just_uploaded_ids=just_uploaded_ids,
    )


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
    redis: RedisDep,
    doc_type_codes: Annotated[list[str] | str | None, Form()] = None,
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

    if len(named_files) > MAX_FILES_PER_UPLOAD:
        ctx = await _documents_panel_ctx(
            db,
            tenant.id,
            upload_errors=[
                {
                    "filename": "—",
                    "error": f"Máximo {MAX_FILES_PER_UPLOAD} ficheros por subida.",
                },
            ],
        )

        return render(
            request,
            full="pages/documents/index.html",
            partial="components/invoices_panel.html",
            ctx=ctx,
        )

    try:
        per_file_types = doc_type_service.resolve_per_file_doc_types(
            file_count=len(named_files),
            doc_type_codes=doc_type_codes,
        )
    except ValidationError:
        ctx = await _documents_panel_ctx(
            db,
            tenant.id,
            upload_errors=[
                {
                    "filename": "—",
                    "error": "Debes indicar el tipo de cada documento.",
                },
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
    ents = await entitlement_service.resolve_entitlements(db, tenant)

    # Ingest secuencial (misma sesión DB); el paralelismo de extracción es ARQ.
    for upload, user_doc_type in zip(named_files, per_file_types, strict=True):
        display_name = upload.filename or "file"

        try:
            data = await read_upload_limited(upload, max_bytes=MAX_FILE_SIZE)
            mime = validate_invoice_upload(display_name, data)
            result = await document_upload_service.ingest_uploaded_document(
                db,
                tenant_id=tenant.id,
                filename=display_name,
                file_bytes=data,
                mime_type=mime,
                doc_type=user_doc_type,
                redis=redis,
                ents=ents,
                user_id=_user.id,
                request_ctx=_audit_request_context(request),
            )
            created_document_ids.append(str(result.record_id))

        except RateLimitError as exc:
            errors.append({"filename": display_name, "error": exc.message})
            logger.warning("upload.rate_limited", filename=display_name, tenant_id=str(tenant.id))

        except UploadValidationError as exc:
            errors.append({"filename": display_name, "error": str(exc)})
            logger.warning("upload.rejected", filename=display_name, error=str(exc))

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


async def _document_row_response(
    request: Request,
    db: AsyncSession,
    tenant_id: UUID,
    *,
    kind: str,
    document_id: UUID,
) -> HTMLResponse:
    if kind == "invoice":
        invoice = await invoice_service.get_invoice(db, tenant_id, document_id)
        document = document_panel_service.row_from_invoice(invoice)
    elif kind == "ticket":
        ticket = await ticket_service.get_ticket(db, tenant_id, document_id)
        document = document_panel_service.row_from_ticket(ticket)
    elif kind == "contract":
        contract = await contract_service.get_contract(db, tenant_id, document_id)
        document = document_panel_service.row_from_contract(contract)
    elif kind == "insurance":
        insurance = await insurance_service.get_insurance(db, tenant_id, document_id)
        document = document_panel_service.row_from_insurance(insurance)
    else:
        raise ValidationError("Tipo de documento no válido.")

    return render(
        request,
        full="components/document_row.html",
        partial="components/document_row.html",
        ctx={
            "document": document,
            "just_uploaded_ids": [],
        },
    )


@router.post("/{kind}/{document_id}/confirm-type")
async def document_confirm_type(
    request: Request,
    kind: str,
    document_id: UUID,
    _user: CurrentUser,
    tenant: CurrentTenant,
    choice: Annotated[str, Form()],
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    if kind not in ("invoice", "ticket"):
        raise ValidationError("Solo facturas y tickets requieren confirmación de tipo.")
    if choice not in ("keep", "suggested"):
        raise ValidationError("Elección de tipo no válida.")

    result = await document_type_confirm_service.confirm_document_type(
        db,
        tenant_id=tenant.id,
        kind=kind,  # type: ignore[arg-type]
        document_id=document_id,
        choice=choice,  # type: ignore[arg-type]
    )
    await db.commit()
    return await _document_row_response(
        request,
        db,
        tenant.id,
        kind=result.kind,
        document_id=result.document_id,
    )


@router.post("/{kind}/{document_id}/retry")
async def document_retry(
    request: Request,
    kind: str,
    document_id: UUID,
    _user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    if kind not in ("invoice", "ticket", "contract", "insurance"):
        raise ValidationError("Tipo de documento no válido.")
    await document_processing_service.retry_processing(
        db,
        tenant_id=tenant.id,
        document_kind=kind,  # type: ignore[arg-type]
        document_id=document_id,
    )
    await db.commit()
    return await _document_row_response(
        request,
        db,
        tenant.id,
        kind=kind,
        document_id=document_id,
    )


@router.post("/{kind}/{document_id}/dismiss")
async def document_dismiss(
    request: Request,
    kind: str,
    document_id: UUID,
    _user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    if kind not in ("invoice", "ticket", "contract", "insurance"):
        raise ValidationError("Tipo de documento no válido.")
    await document_processing_service.dismiss_from_panel(
        db,
        tenant_id=tenant.id,
        document_kind=kind,  # type: ignore[arg-type]
        document_id=document_id,
    )
    await db.commit()
    return HTMLResponse(status_code=200, content="")


def _audit_request_context(request: Request) -> AuditRequestContext:
    forwarded = request.headers.get("x-forwarded-for")
    ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else None)
    )
    return AuditRequestContext(ip=ip, user_agent=request.headers.get("user-agent"))


@router.post("/{kind}/{document_id}/delete")
async def document_delete(
    request: Request,
    kind: str,
    document_id: UUID,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    if kind not in ("invoice", "ticket", "contract", "insurance"):
        raise ValidationError("Tipo de documento no válido.")
    await document_delete_service.delete_document(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        document_kind=kind,  # type: ignore[arg-type]
        document_id=document_id,
        request_ctx=_audit_request_context(request),
    )
    await db.commit()
    return HTMLResponse(status_code=200, content="")
