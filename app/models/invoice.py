from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.doc_type import DocType
    from app.models.llm_call import LLMCall


# StrEnum hace que el valor del enum sea directamente el string (p. ej.
# InvoiceStatus.pending == "pending" es True). Esto simplifica comparaciones
# con valores de BD y serialización JSON sin necesitar .value en cada acceso.
class InvoiceStatus(enum.StrEnum):
    pending = "pending"  # fichero recibido, aún no encolado o en cola
    processing = "processing"  # job ARQ en ejecución, extracción LLM en curso
    ready = "ready"  # extracción completada, datos disponibles
    failed = "failed"  # error en extracción; error_message contiene el detalle
    reviewed = "reviewed"  # un usuario humano revisó y confirmó los datos


class Invoice(Base):
    __tablename__ = "invoices"

    # PG_UUID(as_uuid=True): usa el tipo UUID nativo de Postgres en lugar de
    # CHAR(36). as_uuid=True hace que asyncpg devuelva objetos Python UUID, no
    # strings. default=uuid4 (función, no llamada): SQLAlchemy invoca uuid4()
    # por cada nueva instancia; pasar uuid4() generaría el mismo UUID para todas.
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        # CASCADE: si se borra un tenant (GDPR, baja de cliente), todas sus
        # facturas se eliminan en cascada desde la BD, sin necesidad de lógica
        # adicional en la app.
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        # index=True: tenant_id es el filtro más frecuente en todas las queries;
        # el índice individual se complementa con los compuestos de __table_args__.
        index=True,
    )
    doc_type_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("doc_types.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[InvoiceStatus] = mapped_column(
        # native_enum=True: crea un tipo ENUM en Postgres en lugar de VARCHAR.
        # Aporta validación a nivel de BD (Postgres rechaza valores no definidos)
        # y es más eficiente en almacenamiento. name="invoice_status" es
        # necesario para que Alembic pueda gestionar el tipo por nombre.
        Enum(InvoiceStatus, name="invoice_status", native_enum=True),
        nullable=False,
        default=InvoiceStatus.pending,
    )

    # Nullable: estos campos se rellenan durante create_invoice_from_upload.
    # Un Invoice puede existir sin ellos si se crea por otras vías en el futuro.
    source_file_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_mime: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # Todos los campos de extracción son nullable porque el registro se crea
    # ANTES de que el LLM extraiga los datos (estado pending/processing).
    # Se rellenan en apply_extraction_result cuando el worker termina con éxito.
    fecha: Mapped[date | None] = mapped_column(Date, nullable=True)
    proveedor: Mapped[str | None] = mapped_column(String(300), nullable=True)
    cif_nif: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Numeric(12, 2): hasta 9.999.999.999,99 EUR. Cubre facturas de cualquier
    # tamaño que una pyme pueda recibir. 2 decimales = céntimos de euro.
    base_imponible: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Numeric(5, 2): permite valores de 0,00 a 999,99. El rango real es 0-100
    # pero se usa 5 dígitos para no comprometer tipos de IVA de otros países.
    iva_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    iva_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # String(3): código ISO 4217, siempre 3 caracteres (EUR, USD, GBP…).
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")

    # JSONB (no JSON): almacenamiento binario en Postgres, más eficiente en
    # consultas y soporte para índices GIN. Guarda el output completo de
    # Instructor para auditoría y posibles reintentos sin rellamar al LLM.
    raw_extraction: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Numeric(3, 2): rango 0,00-9,99 pero semánticamente acotado a 0,00-1,00.
    # 2 decimales son suficientes para la precisión que el LLM puede ofrecer.
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Sin ForeignKey explícito a llm_calls: la relación es opcional (puede no
    # existir si el job falló antes de registrar la llamada) y evita dependencias
    # de integridad referencial entre dos tablas de ciclos de vida distintos.
    llm_call_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    llm_call: Mapped[LLMCall | None] = relationship(
        "LLMCall",
        primaryjoin="Invoice.llm_call_id == foreign(LLMCall.id)",
        viewonly=True,
        uselist=False,
    )

    reviewed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        # SET NULL (no CASCADE): si el usuario revisor es eliminado del sistema,
        # la factura y su estado de revisión se conservan; solo se pierde quién
        # la revisó. CASCADE borraría la factura, que sería incorrecto.
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # server_default=func.now(): el timestamp lo asigna Postgres, no Python.
    # Garantiza coherencia entre instancias de la app y el reloj de la BD.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # onupdate=func.now(): SQLAlchemy añade `updated_at = now()` automáticamente
    # en cada UPDATE. No obstante, en el service se establece manualmente también
    # porque algunos drivers async no garantizan que onupdate se dispare siempre.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    doc_type: Mapped[DocType] = relationship(back_populates="invoices")

    lines: Mapped[list[InvoiceLine]] = relationship(
        back_populates="invoice",
        # "all, delete-orphan": si se borra un Invoice, sus líneas se borran en
        # cascada. Si se elimina una línea de invoice.lines (lista Python), también
        # se borra de BD. Esto es lo que permite el patrón de reemplazo total de
        # líneas en apply_extraction_result (invoice.lines = []).
        cascade="all, delete-orphan",
        # String en lugar de clase: evita la referencia circular en tiempo de
        # definición de clase (InvoiceLine aún no está definida aquí).
        order_by="InvoiceLine.position",
    )

    __table_args__ = (
        # Índice compuesto tenant+status: cubre la query más común de la UI
        # ("facturas de este tenant en estado X") y los filtros del worker ARQ.
        Index("ix_invoices_tenant_status", "tenant_id", "status"),
        # Índice compuesto tenant+fecha: cubre filtros por rango de fechas
        # y la futura feature de búsqueda por período en la UI.
        Index("ix_invoices_tenant_fecha", "tenant_id", "fecha"),
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    invoice_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        # CASCADE desde Invoice: si se borra la factura padre, sus líneas
        # se borran automáticamente en BD sin intervención de la app.
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # tenant_id en InvoiceLine (redundante con invoice_id → invoice.tenant_id):
    # es necesario para que la política RLS pueda aplicarse directamente sobre
    # esta tabla sin hacer JOIN a invoices. Cada tabla con datos de cliente
    # debe tener tenant_id propio (arquitectura.md §5).
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    # Numeric(12, 3): 3 decimales para cantidad porque puede ser fraccionaria
    # (p. ej. 1,500 kg, 2,250 horas). Mayor precisión que el total (2 dec).
    cantidad: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    # Numeric(12, 4): 4 decimales para precio unitario porque puede ser muy
    # preciso (precio por gramo, por metro, tarifas horarias fraccionadas).
    precio_unitario: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    # Numeric(12, 2): el total monetario siempre en céntimos de euro.
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # position preserva el orden original de las líneas en el documento.
    # ORDER BY position en queries de detalle reproduce la factura tal como
    # la vio el LLM (y el usuario en el PDF).
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Sin updated_at: las líneas son inmutables una vez creadas. Si cambia la
    # extracción (reintento), el service borra todas las líneas y las recrea;
    # no se actualizan registros individuales.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    invoice: Mapped[Invoice] = relationship(back_populates="lines")
