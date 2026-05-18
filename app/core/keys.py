"""Convenciones de claves de objetos en R2 (facturas, documentos)."""

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
    now = datetime.now(UTC)
    name = PurePosixPath(original_filename).name.replace("/", "_").replace("\\", "_")
    safe = "".join(c for c in name if c.isalnum() or c in "._-")[:120] or "file"
    return f"invoices/{tenant_id}/{now:%Y/%m}/{uuid.uuid4()}-{safe}"


def document_key(tenant_id: uuid.UUID, original_filename: str) -> str:
    """Genera una key para documentos del módulo de conocimiento.

    Estructura: documents/{tenant_id}/{yyyy}/{mm}/{uuid}-{slug_filename}
    """
    now = datetime.now(UTC)
    name = PurePosixPath(original_filename).name.replace("/", "_").replace("\\", "_")
    safe = "".join(c for c in name if c.isalnum() or c in "._-")[:120] or "file"
    return f"documents/{tenant_id}/{now:%Y/%m}/{uuid.uuid4()}-{safe}"
