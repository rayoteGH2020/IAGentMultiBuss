"""Convenciones de claves de objetos en R2 (facturas, documentos).

Las keys son la única referencia al fichero físico: se almacenan en BD
(Invoice.source_file_key, Document.source_file_key) y son la entrada
a todas las operaciones de Storage. La estructura elegida cumple tres objetivos:
  1. Aislamiento por tenant (prefijo tenant_id).
  2. Unicidad garantizada (UUID en el nombre).
  3. Partición temporal para políticas de retención GDPR (yyyy/mm).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath


def invoice_key(tenant_id: uuid.UUID, original_filename: str) -> str:
    """Genera una key estable para una factura subida.

    Estructura: invoices/{tenant_id}/{yyyy}/{mm}/{uuid}-{slug_filename}

    Args:
        tenant_id: Tenant propietario del objeto.
        original_filename: Nombre original del archivo (no rutas completas).

    Returns:
        Key de objeto S3-compatible.
    """
    # UTC explícito: la partición año/mes debe ser consistente
    # independientemente del timezone del servidor.
    now = datetime.now(UTC)
    # PurePosixPath (no Path): funciona igual en Windows y Linux para extraer
    # solo el nombre del fichero. Path("C:\\Users\\...\\factura.pdf").name
    # en Linux devolvería el path completo; PurePosixPath lo maneja siempre
    # correctamente. Los replace adicionales cubren nombres con barras.
    name = PurePosixPath(original_filename).name.replace("/", "_").replace("\\", "_")
    # Sanitización del nombre: S3/R2 acepta casi cualquier carácter en las keys,
    # pero caracteres especiales como `#`, `?`, `&`, espacios y comillas causan
    # problemas en URLs, shells y algunos SDKs. Se conservan solo alfanuméricos
    # y los tres caracteres seguros universales (punto, guion bajo, guion).
    # [:120] limita el nombre del fichero: el prefix (invoices/uuid/yyyy/mm/uuid-)
    # ocupa ~80 chars; S3 tiene un límite de 1024 bytes por key.
    # or "file": fallback si el nombre resultante está vacío (nombre con solo
    # caracteres especiales, p. ej. "###.pdf" → safe="" → key termina en "file").
    safe = "".join(c for c in name if c.isalnum() or c in "._-")[:120] or "file"
    return (
        # invoices/: prefijo de módulo; permite compartir bucket con otros módulos
        # (documents/, exports/) sin colisiones y facilita reglas de ciclo de vida
        # en R2 por prefijo (p. ej. expirar invoices/ a los 7 años).
        # {tenant_id}/: aislamiento por tenant; permite policies de bucket por
        # prefijo y simplifica el borrado massal GDPR de un tenant.
        # {now:%Y/%m}/: partición temporal; el job de retención GDPR puede borrar
        # "todos los objetos de enero 2024 del tenant X" listando ese prefijo.
        # {uuid.uuid4()}-: garantiza unicidad incluso si dos usuarios suben el
        # mismo nombre de fichero simultáneamente; evita sobreescrituras silenciosas.
        # -{safe}: preserva legibilidad en logs y dashboards de R2.
        f"invoices/{tenant_id}/{now:%Y/%m}/{uuid.uuid4()}-{safe}"
    )


def ticket_key(tenant_id: uuid.UUID, original_filename: str) -> str:
    """Genera una key estable para un ticket subido."""
    now = datetime.now(UTC)
    name = PurePosixPath(original_filename).name.replace("/", "_").replace("\\", "_")
    safe = "".join(c for c in name if c.isalnum() or c in "._-")[:120] or "file"
    return f"tickets/{tenant_id}/{now:%Y/%m}/{uuid.uuid4()}-{safe}"


def knowledge_faq_key(tenant_id: uuid.UUID, document_id: uuid.UUID) -> str:
    """Genera una key R2 determinista para el contenido serializado de un FAQ (Paso 21 B).

    Estructura: documents/{tenant_id}/faq/{document_id}.txt
    Determinista por document_id para que las actualizaciones sobreescriban
    el mismo objeto en R2 sin dejar huérfanos.
    """
    return f"documents/{tenant_id}/faq/{document_id}.txt"


def knowledge_url_key(tenant_id: uuid.UUID) -> str:
    """Genera una key R2 para texto scrapeado de una URL (Paso 21 A).

    Estructura: documents/{tenant_id}/url/{yyyy}/{mm}/{uuid}.txt
    El fichero .txt contendrá el texto plano extraído de la URL.
    """
    now = datetime.now(UTC)
    return f"documents/{tenant_id}/url/{now:%Y/%m}/{uuid.uuid4()}.txt"


def document_key(tenant_id: uuid.UUID, original_filename: str) -> str:
    """Genera una key para documentos del módulo de conocimiento (RAG).

    Estructura: documents/{tenant_id}/{yyyy}/{mm}/{uuid}-{slug_filename}

    Función separada de invoice_key aunque comparten la misma lógica de
    sanitización: el prefijo diferente ("documents/" vs "invoices/") permite
    aplicar políticas de retención y permisos distintos por módulo en R2.
    """
    now = datetime.now(UTC)
    name = PurePosixPath(original_filename).name.replace("/", "_").replace("\\", "_")
    safe = "".join(c for c in name if c.isalnum() or c in "._-")[:120] or "file"
    return f"documents/{tenant_id}/{now:%Y/%m}/{uuid.uuid4()}-{safe}"
