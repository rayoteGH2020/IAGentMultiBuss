"""Rutas web del módulo de base de conocimiento RAG (Paso 18).

Patrón página/fragmento en todos los endpoints:
  - render(..., full="pages/knowledge/index.html", partial="components/knowledge_rows.html")
  - Los endpoints de detalle y acciones devuelven siempre fragmento.

Upload: multipart (files[] + kind global para el batch). Validación MIME/tamaño
delegada en knowledge_document_service.create_from_upload, que llama a
validate_knowledge_upload internamente.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import RateLimitError
from app.core.rate_limiter import check_knowledge_upload_rate
from app.core.templating import render
from app.core.uploads import UploadValidationError
from app.deps import CurrentTenant, CurrentUser, RedisDep, get_db
from app.jobs.queue import enqueue_knowledge_indexing
from app.models.knowledge import KnowledgeDocumentKind, KnowledgeDocumentStatus
from app.schemas.knowledge import KnowledgeDocumentFilters
from app.services import knowledge_document_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

# Número máximo de ficheros por subida: igual que el límite de /documents/upload.
_MAX_FILES_PER_UPLOAD = 20


async def _list_ctx(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    filters: KnowledgeDocumentFilters | None = None,
    upload_errors: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    """Contexto compartido por los endpoints de listado y upload."""
    page = await knowledge_document_service.list_documents(db, tenant_id=tenant_id, filters=filters)
    return {
        "documents": page.items,
        "total": page.total,
        "filters": filters or KnowledgeDocumentFilters(),
        "kinds": list(KnowledgeDocumentKind),
        "statuses": list(KnowledgeDocumentStatus),
        "upload_errors": upload_errors or [],
    }


@router.get("")
async def knowledge_index(
    request: Request,
    _user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
    kind: str | None = None,
    status: str | None = None,
) -> HTMLResponse:
    filters = _parse_filters(kind, status)
    ctx = await _list_ctx(db, tenant.id, filters=filters)
    return render(
        request,
        full="pages/knowledge/index.html",
        partial="components/knowledge_rows.html",
        ctx=ctx,
    )


@router.get("/rows")
async def knowledge_rows(
    request: Request,
    _user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
    kind: str | None = None,
    status: str | None = None,
) -> HTMLResponse:
    filters = _parse_filters(kind, status)
    ctx = await _list_ctx(db, tenant.id, filters=filters)
    return render(
        request,
        full="pages/knowledge/index.html",
        partial="components/knowledge_rows.html",
        ctx=ctx,
    )


@router.post("/upload")
async def upload_knowledge(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    redis: RedisDep,
    kind: Annotated[str | None, Form()] = None,
    files: Annotated[list[UploadFile] | None, File(description="Knowledge documents")] = None,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    named_files = [f for f in (files or []) if f.filename]

    # --- Validaciones de batch ---
    if not named_files:
        ctx = await _list_ctx(
            db,
            tenant.id,
            upload_errors=[{"filename": "—", "error": "No se ha seleccionado ningún fichero."}],
        )
        return render(
            request,
            full="pages/knowledge/index.html",
            partial="components/knowledge_rows.html",
            ctx=ctx,
        )

    if len(named_files) > _MAX_FILES_PER_UPLOAD:
        ctx = await _list_ctx(
            db,
            tenant.id,
            upload_errors=[
                {"filename": "—", "error": f"Máximo {_MAX_FILES_PER_UPLOAD} ficheros por subida."}
            ],
        )
        return render(
            request,
            full="pages/knowledge/index.html",
            partial="components/knowledge_rows.html",
            ctx=ctx,
        )

    doc_kind = _parse_kind(kind)
    if doc_kind is None:
        ctx = await _list_ctx(
            db,
            tenant.id,
            upload_errors=[
                {"filename": "—", "error": "Debes seleccionar una categoría para el documento."}
            ],
        )
        return render(
            request,
            full="pages/knowledge/index.html",
            partial="components/knowledge_rows.html",
            ctx=ctx,
        )

    # --- Rate limit diario por tenant ---
    settings = get_settings()
    try:
        await check_knowledge_upload_rate(
            redis,
            tenant_id=tenant.id,
            max_per_day=settings.knowledge_max_uploads_per_day,
            n_files=len(named_files),
        )
    except RateLimitError as exc:
        ctx = await _list_ctx(
            db,
            tenant.id,
            upload_errors=[{"filename": "—", "error": str(exc)}],
        )
        return render(
            request,
            full="pages/knowledge/index.html",
            partial="components/knowledge_rows.html",
            ctx=ctx,
        )

    # --- Procesado por fichero ---
    errors: list[dict[str, str]] = []

    for upload in named_files:
        display_name = upload.filename or "file"
        try:
            data = await upload.read()
            doc = await knowledge_document_service.create_from_upload(
                db,
                tenant_id=tenant.id,
                user_id=user.id,
                filename=display_name,
                file_bytes=data,
                kind=doc_kind,
            )
            # flush() ya fue llamado en create_from_upload; el commit real lo
            # hace get_db al final del handler. El job puede arrancar antes del
            # commit en condición de carga extrema, pero es el mismo patrón
            # aceptado en document_upload_service.py para facturas y tickets.
            await enqueue_knowledge_indexing(doc.id, tenant.id)

        except UploadValidationError as exc:
            errors.append({"filename": display_name, "error": str(exc)})
            logger.warning(
                "knowledge.upload.rejected",
                filename=display_name,
                tenant_id=str(tenant.id),
                error=str(exc),
            )

        except Exception as exc:
            errors.append(
                {
                    "filename": display_name,
                    "error": "Error al procesar el fichero. Inténtalo de nuevo.",
                }
            )
            logger.exception(
                "knowledge.upload.failed",
                filename=display_name,
                tenant_id=str(tenant.id),
                error=str(exc),
            )

    ctx = await _list_ctx(db, tenant.id, upload_errors=errors)
    return render(
        request,
        full="pages/knowledge/index.html",
        partial="components/knowledge_rows.html",
        ctx=ctx,
    )


@router.get("/{document_id}")
async def knowledge_detail(
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
    )
    return render(
        request,
        full="components/knowledge_detail_panel.html",
        partial="components/knowledge_detail_panel.html",
        ctx={"document": doc},
    )


@router.post("/{document_id}/reindex")
async def knowledge_reindex(
    request: Request,
    document_id: UUID,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    doc_orm = await knowledge_document_service.request_reindex(
        db,
        tenant_id=tenant.id,
        document_id=document_id,
        user_id=user.id,
    )
    doc_read = await knowledge_document_service.get_document(
        db,
        tenant_id=tenant.id,
        document_id=doc_orm.id,
        include_download_url=False,
    )
    return render(
        request,
        full="components/knowledge_row.html",
        partial="components/knowledge_row.html",
        ctx={"document": doc_read},
    )


@router.delete("/{document_id}")
async def knowledge_delete(
    request: Request,
    document_id: UUID,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    await knowledge_document_service.delete_document(
        db,
        tenant_id=tenant.id,
        document_id=document_id,
        user_id=user.id,
    )
    # Devuelve cuerpo vacío: HTMX con hx-swap="outerHTML" elimina la fila del DOM.
    return HTMLResponse("")


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------


def _parse_kind(kind_str: str | None) -> KnowledgeDocumentKind | None:
    """Parsea el campo kind del form; devuelve None si ausente o inválido."""
    if not kind_str:
        return None
    try:
        return KnowledgeDocumentKind(kind_str)
    except ValueError:
        return None


def _parse_filters(
    kind_str: str | None,
    status_str: str | None,
) -> KnowledgeDocumentFilters:
    """Construye filtros de listado desde query params; ignora valores inválidos."""
    kind: KnowledgeDocumentKind | None = None
    status: KnowledgeDocumentStatus | None = None
    if kind_str:
        try:
            kind = KnowledgeDocumentKind(kind_str)
        except ValueError:
            pass
    if status_str:
        try:
            status = KnowledgeDocumentStatus(status_str)
        except ValueError:
            pass
    return KnowledgeDocumentFilters(kind=kind, status=status)
