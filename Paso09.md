# Paso 09 — Modelos de facturas, RLS, servicio y listado vacío

## Objetivo

Crear los modelos `Invoice` e `InvoiceLine`, la migración Alembic con RLS activado, el servicio `invoice_service` con operaciones básicas, y la página `/invoices` que lista las facturas del tenant (todavía vacía: ni subida ni extracción).

Al final del paso, navegar a `/invoices` muestra una tabla vacía con un mensaje de "estado vacío" y un CTA preparado para el siguiente paso. La fundación de datos del módulo 1 queda cerrada.

## Pre-requisitos

- Pasos 01-08 completados.
- Migraciones Alembic funcionando contra el contenedor de Postgres.

## Contexto relevante

- `arquitectura.md` sección 5 (Modelos de datos) — tablas `invoices`, `invoice_lines`.
- `arquitectura.md` sección 7 (Seguridad y multi-tenancy) — política RLS estándar.
- `Agents.md` sección 2 (capas), sección 4 (SQLAlchemy 2.0 async).
- `Paso06.md` para referencia del patrón de migración con RLS.

## Tareas

- [ ] Crear `app/models/invoice.py` con `Invoice` e `InvoiceLine`.
- [ ] Exportar los modelos desde `app/models/__init__.py`.
- [ ] Generar migración Alembic: `uv run alembic revision --autogenerate -m "add invoices tables"`.
- [ ] Revisar la migración: comprobar columnas, índices, FKs.
- [ ] Añadir a la migración el `op.execute()` que activa RLS y crea la política `tenant_isolation`.
- [ ] Aplicar migración: `uv run alembic upgrade head`.
- [ ] Crear `app/services/invoice_service.py` con `list_invoices`, `get_invoice`, `create_invoice_stub`.
- [ ] Crear `app/routes/web/invoices.py` con `GET /invoices`.
- [ ] Registrar el router en `app/main.py`.
- [ ] Crear `app/templates/pages/invoices/index.html`.
- [ ] Crear `app/templates/components/empty_state.html` (reutilizable).
- [ ] Actualizar `app/templates/components/sidebar.html` para que la entrada "Facturas" enlace a `/invoices`.
- [ ] Test unitario en `tests/unit/test_invoice_service.py` (al menos `list_invoices` con BD vacía).
- [ ] Test de integración: GET `/invoices` autenticado devuelve 200 y muestra el empty state.
- [ ] Commit: `feat: invoice models, migration with RLS, empty list page`.

## Detalles técnicos

### `app/models/invoice.py`

```python
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class InvoiceStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    ready = "ready"
    failed = "failed"
    reviewed = "reviewed"


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, name="invoice_status"),
        nullable=False,
        default=InvoiceStatus.pending,
    )

    # Fichero origen en R2
    source_file_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_mime: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # Datos extraídos
    fecha: Mapped[date | None] = mapped_column(nullable=True)
    proveedor: Mapped[str | None] = mapped_column(String(300), nullable=True)
    cif_nif: Mapped[str | None] = mapped_column(String(20), nullable=True)
    base_imponible: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    iva_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    iva_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")

    # Trazabilidad
    raw_extraction: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Revisión humana
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLine.position",
    )

    __table_args__ = (
        Index("ix_invoices_tenant_status", "tenant_id", "status"),
        Index("ix_invoices_tenant_fecha", "tenant_id", "fecha"),
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    cantidad: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    precio_unitario: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    position: Mapped[int] = mapped_column(nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    invoice: Mapped[Invoice] = relationship(back_populates="lines")
```

### Exportación en `app/models/__init__.py`

```python
from app.models.base import Base
from app.models.tenant import Tenant
from app.models.user import User
from app.models.membership import Membership
from app.models.invoice import Invoice, InvoiceLine, InvoiceStatus

__all__ = [
    "Base",
    "Tenant",
    "User",
    "Membership",
    "Invoice",
    "InvoiceLine",
    "InvoiceStatus",
]
```

### Migración Alembic

Tras `uv run alembic revision --autogenerate -m "add invoices tables"`, abre el fichero generado en `migrations/versions/` y revísalo. Añade al final de `upgrade()` y al principio de `downgrade()` el bloque RLS:

```python
def upgrade() -> None:
    # ... operaciones generadas por autogenerate ...

    # RLS para invoices
    op.execute("ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY tenant_isolation ON invoices
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
    """)

    # RLS para invoice_lines
    op.execute("ALTER TABLE invoice_lines ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY tenant_isolation ON invoice_lines
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON invoice_lines;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON invoices;")
    op.execute("ALTER TABLE invoice_lines DISABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE invoices DISABLE ROW LEVEL SECURITY;")

    # ... operaciones de downgrade generadas ...
```

### `app/services/invoice_service.py`

```python
from __future__ import annotations

import uuid
from typing import Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import NotFoundError
from app.models import Invoice, InvoiceStatus

logger = structlog.get_logger(__name__)


async def list_invoices(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    status: InvoiceStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Invoice]:
    stmt = (
        select(Invoice)
        .where(Invoice.tenant_id == tenant_id)
        .order_by(Invoice.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status is not None:
        stmt = stmt.where(Invoice.status == status)
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_invoice(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
) -> Invoice:
    stmt = (
        select(Invoice)
        .where(Invoice.tenant_id == tenant_id, Invoice.id == invoice_id)
        .options(selectinload(Invoice.lines))
    )
    result = await db.execute(stmt)
    invoice = result.scalar_one_or_none()
    if invoice is None:
        raise NotFoundError(f"Invoice {invoice_id} not found")
    return invoice


async def create_invoice_stub(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    source_file_key: str,
    source_filename: str,
    source_mime: str,
) -> Invoice:
    """Crea una factura en estado pending, sin datos extraídos aún.

    Usada al subir un fichero antes de encolar el job de extracción.
    """
    invoice = Invoice(
        tenant_id=tenant_id,
        status=InvoiceStatus.pending,
        source_file_key=source_file_key,
        source_filename=source_filename,
        source_mime=source_mime,
    )
    db.add(invoice)
    await db.flush()
    logger.info("invoice.created", invoice_id=str(invoice.id), tenant_id=str(tenant_id))
    return invoice
```

### `app/routes/web/invoices.py`

```python
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import render
from app.deps import current_tenant, get_db
from app.models import Tenant
from app.services import invoice_service

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get("")
async def invoices_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(current_tenant),
):
    invoices = await invoice_service.list_invoices(db, tenant.id)
    return render(
        request,
        full="pages/invoices/index.html",
        partial="components/invoices_table.html",
        ctx={"invoices": invoices, "tenant": tenant},
    )
```

Y registra el router en `app/main.py`:

```python
from app.routes.web import invoices as invoices_routes
# ...
app.include_router(invoices_routes.router)
```

### `app/templates/pages/invoices/index.html`

```html
{% extends "layouts/dashboard.html" %}

{% block title %}Facturas{% endblock %}

{% block content %}
<div class="flex items-center justify-between mb-6">
  <div>
    <h1 class="text-2xl font-semibold text-slate-900">Facturas</h1>
    <p class="text-sm text-slate-500 mt-1">
      Sube facturas y tickets en PDF o foto. Las extraemos automáticamente.
    </p>
  </div>
  <button
    class="px-4 py-2 bg-slate-300 text-slate-600 rounded-md cursor-not-allowed"
    disabled
    title="Disponible en el siguiente paso">
    Subir facturas
  </button>
</div>

{% include "components/invoices_table.html" %}
{% endblock %}
```

### `app/templates/components/invoices_table.html`

```html
{% if invoices %}
<div class="bg-white rounded-lg border border-slate-200 overflow-hidden">
  <table class="w-full text-sm">
    <thead class="bg-slate-50 text-slate-600 text-left">
      <tr>
        <th class="px-4 py-3 font-medium">Fecha</th>
        <th class="px-4 py-3 font-medium">Proveedor</th>
        <th class="px-4 py-3 font-medium">CIF/NIF</th>
        <th class="px-4 py-3 font-medium text-right">Total</th>
        <th class="px-4 py-3 font-medium">Estado</th>
      </tr>
    </thead>
    <tbody class="divide-y divide-slate-100">
      {% for inv in invoices %}
      <tr class="hover:bg-slate-50">
        <td class="px-4 py-3">{{ inv.fecha or '—' }}</td>
        <td class="px-4 py-3">{{ inv.proveedor or '—' }}</td>
        <td class="px-4 py-3 font-mono text-xs">{{ inv.cif_nif or '—' }}</td>
        <td class="px-4 py-3 text-right">
          {% if inv.total %}{{ "%.2f"|format(inv.total) }} €{% else %}—{% endif %}
        </td>
        <td class="px-4 py-3">
          <span class="inline-block px-2 py-0.5 text-xs rounded-full
            {% if inv.status.value == 'ready' %}bg-green-100 text-green-700
            {% elif inv.status.value == 'failed' %}bg-red-100 text-red-700
            {% elif inv.status.value == 'processing' %}bg-blue-100 text-blue-700
            {% else %}bg-slate-100 text-slate-600{% endif %}">
            {{ inv.status.value }}
          </span>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% else %}
  {% include "components/empty_state.html" %}
{% endif %}
```

### `app/templates/components/empty_state.html`

```html
<div class="bg-white border border-dashed border-slate-300 rounded-lg p-12 text-center">
  <svg class="mx-auto h-12 w-12 text-slate-400" fill="none" viewBox="0 0 24 24"
       stroke-width="1.5" stroke="currentColor">
    <path stroke-linecap="round" stroke-linejoin="round"
          d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z" />
  </svg>
  <h3 class="mt-4 text-sm font-medium text-slate-900">Aún no hay facturas</h3>
  <p class="mt-1 text-sm text-slate-500">
    Cuando subas la primera factura aparecerá aquí extraída automáticamente.
  </p>
</div>
```

### Sidebar

En `app/templates/components/sidebar.html`, asegúrate de que la entrada "Facturas" apunta a `/invoices` con `hx-boost="true"`.

### Test unitario

`tests/unit/test_invoice_service.py`:

```python
import uuid
import pytest

from app.services import invoice_service


@pytest.mark.asyncio
async def test_list_invoices_empty(db_session, tenant_factory):
    tenant = await tenant_factory()
    invoices = await invoice_service.list_invoices(db_session, tenant.id)
    assert list(invoices) == []
```

Asume que `tests/conftest.py` ya provee `db_session` y `tenant_factory` desde el Paso 06.

## Criterios de aceptación

- `uv run alembic upgrade head` aplica la migración sin error.
- `psql` ejecutando `\d invoices` muestra columnas y RLS habilitado.
- Test de aislamiento del Paso 06 sigue pasando (verifica RLS global).
- GET `/invoices` autenticado devuelve 200 y muestra empty state.
- GET `/invoices` con `HX-Request: true` devuelve solo el fragmento de tabla.
- `uv run pytest tests/unit/test_invoice_service.py` pasa.
- `uv run ruff check . && uv run mypy app` pasan.

## Comandos útiles

```bash
# Generar migración
uv run alembic revision --autogenerate -m "add invoices tables"

# Aplicar
uv run alembic upgrade head

# Revertir si te equivocas
uv run alembic downgrade -1

# Verificar RLS
docker compose exec postgres psql -U postgres -d saas \
  -c "SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname='public';"
```

## Lo que NO toca este paso

- Subida de archivos (Paso 13).
- Cliente LLM (Paso 10).
- Storage R2 (Paso 11).
- Schema Pydantic `Factura` para Instructor (Paso 12).
- Worker de extracción (Paso 14).

## Posibles problemas

- **`Enum` de Postgres duplicado al volver a migrar**: si bajaste y subiste, puede quedar el tipo. `DROP TYPE invoice_status;` manualmente o ajusta la migración.
- **Autogenerate detecta cambios que no quieres**: revisa siempre el fichero generado y borra líneas espurias.
- **RLS bloquea inserciones en tests**: el `db_session` de tests debe setear `app.current_tenant` antes de insertar, o usar un rol `BYPASSRLS` solo para tests.

## Siguiente paso

`Paso10.md` — Cliente LLM unificado (Anthropic + Google), modelo `LLMCall`, carga de prompts versionados desde fichero, tabla de pricing, integración con Langfuse para tracing.
