"""SADM — revisión y procesado excepcional de documentos rechazados."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Form, Path, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import render
from app.deps import CurrentUser, SuperAdmin, get_db_no_tenant
from app.services import document_override_service, processing_charge_service

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/sadm/documents", tags=["sadm"])

DocumentKindPath = Annotated[Literal["invoice", "ticket", "contract", "insurance"], Path()]


@router.get("", response_class=HTMLResponse)
async def list_rejected(
    request: Request,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    await document_override_service.enable_superadmin_lookup(db)
    documents = await document_override_service.list_rejected_documents(db)
    charges = await processing_charge_service.list_charges(db, limit=20)
    return render(
        request,
        full="pages/sadm/documents/index.html",
        partial="pages/sadm/documents/_list.html",
        ctx={"documents": documents, "charges": charges},
    )


@router.get("/{kind}/{document_id}", response_class=HTMLResponse)
async def review_document(
    request: Request,
    kind: DocumentKindPath,
    document_id: UUID,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    review = await document_override_service.build_review(
        db,
        kind=kind,
        document_id=document_id,
    )
    return render(
        request,
        full="pages/sadm/documents/review.html",
        partial="pages/sadm/documents/_review.html",
        ctx={"review": review},
    )


@router.get("/{kind}/{document_id}/file")
async def open_original(
    kind: DocumentKindPath,
    document_id: UUID,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> RedirectResponse:
    """Redirige a la URL prefirmada del original para revisarlo antes de decidir."""
    url = await document_override_service.original_file_url(
        db,
        kind=kind,
        document_id=document_id,
    )
    return RedirectResponse(url=url, status_code=302)


@router.post("/{kind}/{document_id}/process", response_class=HTMLResponse)
async def authorize_processing(
    request: Request,
    kind: DocumentKindPath,
    document_id: UUID,
    user: CurrentUser,
    _admin: SuperAdmin,
    reason: Annotated[str, Form()] = "",
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    review = await document_override_service.authorize_processing(
        db,
        kind=kind,
        document_id=document_id,
        authorized_by=user.id,
        reason=reason.strip() or None,
    )
    # authorize_processing hace commit: la nueva transacción arranca sin flag
    # de lectura cross-tenant, así que hay que reactivarlo antes de releer.
    await document_override_service.enable_superadmin_lookup(db)
    documents = await document_override_service.list_rejected_documents(db)
    charges = await processing_charge_service.list_charges(db, limit=20)
    return render(
        request,
        full="pages/sadm/documents/index.html",
        partial="pages/sadm/documents/_list.html",
        ctx={"documents": documents, "charges": charges, "authorized": review},
    )
