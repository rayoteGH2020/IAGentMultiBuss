"""Catálogo de tipos de documento administrativo."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from app.core.errors import NotFoundError, ValidationError
from app.models import DocType, DocTypeCode

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


async def get_doc_type_id(
    db: AsyncSession,
    code: DocTypeCode,
) -> UUID:
    """Resuelve el UUID de un tipo de documento por código estable."""
    stmt = select(DocType.id).where(DocType.code == code.value, DocType.is_active.is_(True))
    result = await db.execute(stmt)
    doc_type_id = result.scalar_one_or_none()
    if doc_type_id is None:
        raise NotFoundError(f"DocType {code.value!r} not found")
    return doc_type_id


async def list_active_doc_types(db: AsyncSession) -> list[DocType]:
    """Catálogo activo para selects de UI."""
    stmt = select(DocType).where(DocType.is_active.is_(True)).order_by(DocType.name)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def resolve_active_doc_type(db: AsyncSession, code: str) -> DocType:
    """Resuelve un tipo activo por código (chat y tools validan contra BD)."""
    normalized = code.strip().lower()
    stmt = select(DocType).where(DocType.code == normalized, DocType.is_active.is_(True))
    result = await db.execute(stmt)
    doc_type = result.scalar_one_or_none()
    if doc_type is None:
        raise ValidationError(f"Unknown or inactive document type: {code!r}")
    return doc_type


async def list_doc_type_codes(db: AsyncSession) -> list[str]:
    """Códigos activos del catálogo (validación y tests)."""
    stmt = select(DocType.code).where(DocType.is_active.is_(True)).order_by(DocType.code)
    result = await db.execute(stmt)
    return list(result.scalars().all())


def parse_doc_type_form_value(raw: str | None) -> DocTypeCode | None:
    """Normaliza el valor del formulario de subida."""
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value:
        return None
    try:
        return DocTypeCode(value)
    except ValueError as exc:
        raise ValidationError(f"Unsupported document type: {raw!r}") from exc


def require_doc_type_form_value(raw: str | None) -> DocTypeCode:
    """Exige un tipo de documento válido en el formulario de subida."""
    doc_type = parse_doc_type_form_value(raw)
    if doc_type is None:
        raise ValidationError("Document type is required")
    return doc_type
