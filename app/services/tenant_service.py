"""Lógica de negocio para tenants (multi-tenant)."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.models import Tenant


async def update_tenant_display_name(
    db: AsyncSession,
    tenant: Tenant,
    name: str,
    *,
    min_length: int = 2,
    max_length: int = 120,
) -> Tenant:
    """Persiste el nombre visible del tenant (organización).

    Args:
        db: Sesión async con contexto RLS ya aplicado.
        tenant: Instancia cargada para el tenant actual.
        name: Nuevo nombre (se normaliza strip).
        min_length: Longitud mínima válida (validación repetida desde la ruta).
        max_length: Longitud máxima válida.

    Returns:
        El mismo objeto `tenant` con `name` actualizado tras flush.

    Raises:
        ValidationError: Si el nombre queda fuera del rango permitido después de strip.
    """
    cleaned = name.strip()
    if len(cleaned) < min_length or len(cleaned) > max_length:
        raise ValidationError(
            "Invalid organization name length",
            details={"min_length": min_length, "max_length": max_length},
        )
    tenant.name = cleaned
    await db.flush()
    return tenant
