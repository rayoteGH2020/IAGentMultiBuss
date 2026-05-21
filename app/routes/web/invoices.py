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
    # El prefijo _ indica que el parámetro es requerido por FastAPI (fuerza la
    # dependencia de auth y valida el JWT/tenant) pero no se usa en el cuerpo
    # del handler. Sin él, AuthMiddleware no garantizaría que el usuario tiene
    # membership activa en este tenant.
    _user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    invoices = await invoice_service.list_invoices(db, tenant.id)
    # render() aplica el patrón página/fragmento (Agents.md §6):
    # - Sin header HX-Request (visita directa, F5): devuelve la página completa
    #   con layout, sidebar, etc. (pages/invoices/index.html).
    # - Con HX-Request (navegación HTMX): devuelve solo el panel interior
    #   (components/invoices_panel.html) para intercambiar en el DOM.
    # upload_errors y just_uploaded_ids vacíos: el template siempre espera
    # estas claves para no romper con Jinja; en GET no hay subida reciente.
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
    # Endpoint de refresco de tabla: HTMX lo llama cuando quiere actualizar la
    # lista completa sin recargar la página (p. ej. tras confirmar una edición
    # inline o filtrar). El limit=50 es más restrictivo que en invoices_index
    # para que la respuesta sea rápida; la paginación futura se añadirá aquí.
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
    # Validaciones de límite a nivel de request antes de tocar disco o BD.
    # El límite de 20 ficheros por subida evita que un solo POST consuma demasiada
    # memoria RAM (cada fichero se carga en memoria para leer sus bytes).
    if not files:
        raise ValidationError("No files provided")
    if len(files) > 20:
        raise ValidationError("Max 20 files per upload")

    created = []
    errors: list[dict[str, str]] = []

    # Se procesa cada fichero individualmente para conseguir éxito parcial:
    # si 3 de 5 ficheros son válidos, esos 3 se suben y encolan; los 2
    # inválidos se reportan como errores sin cancelar los demás.
    for upload in files:
        try:
            data = await upload.read()
            # validate_invoice_upload comprueba MIME type y magic bytes antes
            # de escribir nada en R2 o BD; rechaza PDFs falsos (con extensión
            # .pdf pero contenido arbitrario) y tipos no permitidos.
            mime = validate_invoice_upload(upload.filename or "file", data)
            # create_invoice_from_upload: sube el fichero a R2 y crea el
            # registro Invoice en BD con status=pending. No hace la extracción
            # LLM; eso lo hace el worker ARQ de forma asíncrona.
            invoice = await invoice_service.create_invoice_from_upload(
                db,
                tenant_id=tenant.id,
                filename=upload.filename or "file",
                file_bytes=data,
                mime_type=mime,
            )
            # flush (no commit) para que el registro de Invoice tenga ID
            # asignado en la sesión actual sin cerrar la transacción. Es
            # necesario porque enqueue_invoice_processing necesita el invoice.id
            # real para que el worker pueda recuperarlo de BD.
            await db.flush()
            # Encola el job ARQ. El worker descargará el fichero de R2,
            # llamará a extract_invoice y actualizará el Invoice con el resultado.
            await enqueue_invoice_processing(invoice.id, tenant.id)
            created.append(invoice)
        except UploadValidationError as exc:
            # UploadValidationError es por fichero (MIME inválido, tamaño
            # excesivo, etc.): se recoge en la lista de errores y se continúa
            # con el siguiente fichero en lugar de abortar todo el request.
            errors.append(
                {"filename": upload.filename or "file", "error": str(exc)},
            )
            logger.warning(
                "upload.rejected",
                filename=upload.filename,
                error=str(exc),
            )

    # Se refresca la lista completa tras el upload para que la respuesta HTML
    # muestre las nuevas filas (en estado pending/processing) junto con las
    # existentes, sin que el cliente tenga que hacer otra petición.
    invoices = await invoice_service.list_invoices(db, tenant.id, limit=50)
    return render(
        request,
        full="pages/invoices/index.html",
        partial="components/invoices_panel.html",
        ctx={
            "invoices": invoices,
            # El template usa upload_errors para mostrar alertas por fichero
            # rechazado en el panel, sin bloquear las subidas exitosas.
            "upload_errors": errors,
            # just_uploaded_ids permite al template resaltar visualmente
            # (p. ej. fondo amarillo) las filas recién creadas para que el
            # usuario las localice de un vistazo.
            "just_uploaded_ids": [str(i.id) for i in created],
        },
    )
