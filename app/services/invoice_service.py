"""Facturas — listado y creación inicial (stub) antes del pipeline LLM/upload."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.datetime_display import display_today
from app.core.errors import NotFoundError
from app.core.keys import invoice_key
from app.core.storage import get_storage
from app.models import Invoice, InvoiceLine, InvoiceStatus

# Los imports dentro de TYPE_CHECKING solo se evalúan por mypy/pyright, no en
# runtime. Permite usar anotaciones de Sequence, UUID, AsyncSession y Factura
# sin introducir dependencias circulares ni coste de importación en producción.
# `from __future__ import annotations` (arriba) hace que Python trate todas las
# anotaciones como strings hasta que se evalúen explícitamente, lo que es
# compatible con este patrón.
if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.schemas.invoice import Factura

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class InvoiceDateStats:
    """Contadores de facturas por `Invoice.fecha` (fecha del documento, no subida)."""

    current_month: int
    last_30_days: int


async def _count_invoices_with_fecha_in_range(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    start: date,
    end: date,
) -> int:
    stmt = (
        select(func.count())
        .select_from(Invoice)
        .where(
            Invoice.tenant_id == tenant_id,
            Invoice.fecha.is_not(None),
            Invoice.fecha >= start,
            Invoice.fecha <= end,
        )
    )
    result = await db.execute(stmt)
    return int(result.scalar_one())


async def get_invoice_date_stats(
    db: AsyncSession,
    tenant_id: UUID,
) -> InvoiceDateStats:
    """Cuenta facturas por fecha de documento en el mes actual y en los últimos 30 días."""
    today = display_today()
    month_start = today.replace(day=1)
    month_end = today.replace(
        day=calendar.monthrange(today.year, today.month)[1],
    )
    last_30_start = today - timedelta(days=29)

    current_month = await _count_invoices_with_fecha_in_range(
        db,
        tenant_id,
        start=month_start,
        end=month_end,
    )
    last_30_days = await _count_invoices_with_fecha_in_range(
        db,
        tenant_id,
        start=last_30_start,
        end=today,
    )
    return InvoiceDateStats(
        current_month=current_month,
        last_30_days=last_30_days,
    )


async def list_invoices(
    db: AsyncSession,
    tenant_id: UUID,
    # El `*` fuerza que `status`, `limit` y `offset` sean keyword-only.
    # Evita errores de orden de parámetros al llamar a la función
    # (p. ej. `list_invoices(db, tid, 50)` pasaría 50 como `status`).
    *,
    status: InvoiceStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Invoice]:
    stmt = (
        select(Invoice)
        # Filtro explícito de tenant aunque RLS lo aplique automáticamente:
        # defensa en profundidad (Agents.md §7) y mejor rendimiento (el índice
        # sobre tenant_id se usa incluso si RLS está activo).
        .where(Invoice.tenant_id == tenant_id)
        # Más reciente primero: la UI muestra las facturas en orden cronológico
        # inverso para que las recién subidas aparezcan en la parte superior.
        .order_by(Invoice.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status is not None:
        # El filtro de status se aplica condicionalmente para que la misma
        # función sirva tanto para "todas las facturas" (UI principal) como
        # para subconjuntos específicos (p. ej. solo pending en el worker).
        stmt = stmt.where(Invoice.status == status)
    stmt = stmt.options(selectinload(Invoice.lines), selectinload(Invoice.llm_call))
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_invoice(
    db: AsyncSession,
    tenant_id: UUID,
    invoice_id: UUID,
) -> Invoice:
    stmt = (
        select(Invoice)
        # tenant_id en la condición: si un usuario de otro tenant adivinase un
        # UUID válido, recibiría NotFoundError en lugar de la factura. Evita
        # que los errores de RLS (que devuelven fila vacía) filtren información
        # sobre la existencia del recurso en otros tenants.
        .where(Invoice.tenant_id == tenant_id, Invoice.id == invoice_id)
        # Eager load de líneas en la misma query (JOIN en lugar de N+1):
        # tanto el template de detalle como el endpoint de polling necesitan
        # las líneas para renderizar la fila completa.
        .options(selectinload(Invoice.lines), selectinload(Invoice.llm_call))
    )
    result = await db.execute(stmt)
    # scalar_one_or_none devuelve None si no hay resultado, en lugar de lanzar
    # la excepción genérica de SQLAlchemy. Se convierte a NotFoundError de
    # dominio para que el error handler de FastAPI devuelva 404 consistente.
    invoice = result.scalar_one_or_none()
    if invoice is None:
        raise NotFoundError(f"Invoice {invoice_id} not found")
    return invoice


async def create_invoice_stub(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    source_file_key: str,
    source_filename: str,
    source_mime: str,
) -> Invoice:
    """Crea una factura en estado pending, sin datos extraídos.

    El "stub" es el registro mínimo que ancla el fichero al tenant antes de que
    empiece el pipeline LLM. Permite mostrar la fila en la UI inmediatamente
    (estado pending) mientras el worker procesa en segundo plano.
    """
    invoice = Invoice(
        tenant_id=tenant_id,
        # pending: el fichero está en R2 pero aún no hay extracción LLM.
        # El worker cambiará este estado a processing y luego a ready o failed.
        status=InvoiceStatus.pending,
        source_file_key=source_file_key,
        source_filename=source_filename,
        source_mime=source_mime,
    )
    db.add(invoice)
    # flush sin commit: sincroniza el objeto con la BD y asigna el UUID
    # generado por Postgres, pero no cierra la transacción. El llamador
    # (create_invoice_from_upload o el route) decide cuándo hacer commit.
    await db.flush()
    logger.info("invoice.created", invoice_id=str(invoice.id), tenant_id=str(tenant_id))
    return invoice


async def create_invoice_from_upload(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    filename: str,
    file_bytes: bytes,
    mime_type: str,
) -> Invoice:
    """Sube bytes a R2 y crea `Invoice` en estado procesando.

    Orden deliberado: primero R2, luego BD. Si el upload a R2 falla, no queda
    ningún registro huérfano en BD. Si la BD falla tras el upload, queda un
    fichero huérfano en R2 (aceptable: no se referencia desde ningún tenant,
    y el job de limpieza GDPR lo eliminará según la política de retención).
    """
    storage = get_storage()
    # invoice_key genera una ruta namespaced por tenant para evitar colisiones
    # y facilitar la gestión de permisos en R2 (p. ej. prefijos por tenant).
    key = invoice_key(tenant_id, filename)
    await storage.upload_bytes(key, file_bytes, content_type=mime_type)
    invoice = await create_invoice_stub(
        db,
        tenant_id,
        source_file_key=key,
        # Truncado a 300 chars para ajustarse al límite de la columna en BD.
        # Nombres de fichero extremadamente largos son raros pero posibles.
        source_filename=filename[:300],
        source_mime=mime_type,
    )
    # El stub se crea con status=pending; aquí se avanza a processing para
    # reflejar que el fichero ya está en R2 y el job está a punto de encolarse.
    # La UI diferencia pending (esperando subida) de processing (en extracción).
    invoice.status = InvoiceStatus.processing
    await db.flush()
    return invoice


async def apply_extraction_result(
    db: AsyncSession,
    *,
    invoice: Invoice,
    factura: Factura,
    llm_call_id: UUID,
) -> Invoice:
    """Persiste el resultado structured output del extractor LLM sobre `invoice`."""
    invoice.llm_call_id = llm_call_id
    invoice.fecha = factura.fecha
    # Truncados a límites de columna. Los valores vienen del LLM y pueden ser
    # arbitrariamente largos si el modelo extrae texto de contexto adicional.
    invoice.proveedor = factura.proveedor[:300]
    invoice.cif_nif = factura.cif_nif
    invoice.base_imponible = factura.base_imponible
    invoice.iva_percent = factura.iva_percent
    invoice.iva_amount = factura.iva_amount
    invoice.total = factura.total
    # ISO 4217: los códigos de moneda son siempre 3 caracteres (EUR, USD…).
    # El truncado protege ante respuestas inesperadas del LLM.
    invoice.currency = factura.currency[:3]
    # El LLM devuelve confidence como float (p. ej. 0.9700000001 por aritmética
    # de punto flotante). Se convierte a str antes de pasar a Decimal para evitar
    # que la representación binaria imprecisa del float se propague al Decimal.
    # quantize("0.01") normaliza a 2 decimales, que es la precisión de la columna.
    invoice.confidence = Decimal(str(factura.confidence)).quantize(Decimal("0.01"))
    # raw_extraction guarda el JSON completo de Factura como JSONB: sirve de
    # fuente de verdad para auditoría, para reintentos futuros sin rellamar al
    # LLM, y para que el usuario pueda ver exactamente lo que devolvió el modelo.
    invoice.raw_extraction = factura.model_dump(mode="json")
    invoice.status = InvoiceStatus.ready
    invoice.updated_at = datetime.now(tz=UTC)
    # Se limpia error_message por si esta función se invoca en un reintento
    # después de un fallo previo (el invoice estaría en estado failed con
    # error_message relleno).
    invoice.error_message = None

    # Estrategia de reemplazo de líneas:
    # 1. Se vacía la lista en memoria (invoice.lines = []).
    # 2. Se hace flush para que SQLAlchemy emita los DELETE de las líneas
    #    anteriores a BD (via CASCADE configurado en la relación).
    # 3. Se insertan las líneas nuevas. Así un reintento no duplica líneas.
    invoice.lines = []
    await db.flush()
    for idx, linea in enumerate(factura.lineas):
        invoice.lines.append(
            InvoiceLine(
                tenant_id=invoice.tenant_id,
                descripcion=linea.descripcion[:1000],
                cantidad=linea.cantidad,
                precio_unitario=linea.precio_unitario,
                total=linea.total,
                # position preserva el orden de las líneas tal como aparecen
                # en la factura original; ORDER BY position en queries de detalle.
                position=idx,
            ),
        )
    await db.flush()
    return invoice


async def mark_failed(
    db: AsyncSession,
    *,
    invoice_id: UUID,
    tenant_id: UUID,
    error: str,
    llm_call_id: UUID | None = None,
) -> None:
    """Marca una factura como fallida tras error en pipeline (worker/jobs)."""
    # Se usa get_invoice (que incluye el filtro de tenant) en lugar de un UPDATE
    # directo, para garantizar que el registro existe y pertenece al tenant
    # correcto antes de modificarlo. Evita actualizaciones ciegas.
    invoice = await get_invoice(db, tenant_id, invoice_id)
    invoice.status = InvoiceStatus.failed
    if llm_call_id is not None:
        invoice.llm_call_id = llm_call_id
    # Truncado a 2000 chars: el mensaje de error puede incluir trazas completas
    # de Python. Se guarda suficiente contexto para diagnosticar sin llenar la BD.
    invoice.error_message = error[:2000]
    # updated_at explícito: SQLAlchemy no actualiza onupdate automáticamente en
    # todos los drivers async; se establece manualmente para coherencia.
    invoice.updated_at = datetime.now(tz=UTC)
