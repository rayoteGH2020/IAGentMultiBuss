"""Rastreo en ``audit_log`` a partir de un email, un nombre o un fichero (P2c-7, SADM).

La metadata de auditoría guarda seudónimos (``app/core/audit_pseudonym.py``), no
el dato en claro. Para responder «qué se hizo con ana@empresa.com» se recalcula
el seudónimo y se busca:

- **Lo que se hizo con la persona:** entradas cuya metadata lleva ese seudónimo
  (altas, bajas y solicitudes de miembro; calendario).
- **Lo que hizo ella:** entradas con su ``user_id``. Se obtiene de ``users`` si la
  cuenta sigue activa o, si se anonimizó (D021), de las membresías de esas
  entradas: así se rastrea también a un usuario borrado si se conoce su email.
- **Ficheros:** por seudónimo del nombre o por SHA-256 del contenido.

Solo lectura salvo el propio rastreo, que queda auditado en cada tenant con
resultados (``sadm.audit_lookup``, AGENTS.md §7). Lo usa
``scripts/audit_lookup.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import ColumnElement, func, or_, select

from app.core.audit_pseudonym import audit_ref
from app.core.db import set_tenant_context
from app.models import AuditLog, Membership, User
from app.services import audit_service
from app.services.document_override_service import enable_superadmin_lookup

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

ACTION_AUDIT_LOOKUP = "sadm.audit_lookup"
RESOURCE_TENANT = "tenant"
MAX_ENTRIES = 2_000


@dataclass(frozen=True, slots=True)
class LookupCriteria:
    """Lo que se sabe de la persona o del fichero. Al menos un campo."""

    email: str | None = None
    name: str | None = None
    filename: str | None = None
    sha256: str | None = None
    tenant_id: UUID | None = None

    def labels(self) -> list[str]:
        return [
            label
            for label, value in (
                ("email", self.email),
                ("name", self.name),
                ("filename", self.filename),
                ("sha256", self.sha256),
            )
            if value
        ]


@dataclass(frozen=True, slots=True)
class AuditEntry:
    created_at: datetime
    tenant_id: UUID
    action: str
    resource_type: str
    resource_id: UUID | None
    user_id: UUID | None
    ip: str | None
    metadata: dict[str, Any]


@dataclass(slots=True)
class LookupResult:
    user_ids: set[UUID] = field(default_factory=set)
    entries: list[AuditEntry] = field(default_factory=list)


def _meta(key: str) -> ColumnElement[str]:
    # metadata_ es JSONB; ->> devuelve el valor como texto.
    return cast("ColumnElement[str]", AuditLog.metadata_[key].astext)


def _metadata_conditions(criteria: LookupCriteria) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if criteria.email:
        ref = audit_ref(criteria.email, "email")
        conditions += [
            _meta("email_ref") == ref,
            _meta("google_email_ref") == ref,
            # Entradas anteriores a P2c-7, con el email en claro (solo dev).
            func.lower(_meta("email")) == criteria.email.strip().lower(),
        ]
    if criteria.name:
        conditions += [
            _meta("display_name_ref") == audit_ref(criteria.name, "name"),
            func.lower(_meta("display_name")) == criteria.name.strip().lower(),
        ]
    if criteria.filename:
        conditions += [
            _meta("filename_ref") == audit_ref(criteria.filename, "filename"),
            _meta("filename") == criteria.filename.strip(),
        ]
    if criteria.sha256:
        conditions.append(_meta("file_sha256") == criteria.sha256.strip().lower())
    return conditions


async def _users_by_identity(db: AsyncSession, criteria: LookupCriteria) -> set[UUID]:
    """Cuentas activas por email exacto o por nombre (puede dar varias)."""
    conditions: list[ColumnElement[bool]] = []
    if criteria.email:
        conditions.append(func.lower(User.email) == criteria.email.strip().lower())
    if criteria.name:
        conditions.append(User.name.ilike(f"%{criteria.name.strip()}%"))
    if not conditions:
        return set()
    return set((await db.execute(select(User.id).where(or_(*conditions)))).scalars().all())


async def _users_from_memberships(db: AsyncSession, entries: list[AuditEntry]) -> set[UUID]:
    """Usuario de cada membresía encontrada (también si se anonimizó, D021)."""
    user_ids: set[UUID] = set()
    for entry in entries:
        if entry.resource_type != "membership" or entry.resource_id is None:
            continue
        await set_tenant_context(db, str(entry.tenant_id))
        user_id = await db.scalar(
            select(Membership.user_id).where(Membership.id == entry.resource_id)
        )
        if user_id is not None:
            user_ids.add(user_id)
    return user_ids


async def _entries(
    db: AsyncSession,
    criteria: LookupCriteria,
    conditions: list[ColumnElement[bool]],
) -> list[AuditEntry]:
    if not conditions:
        return []
    await enable_superadmin_lookup(db)
    stmt = select(AuditLog).where(or_(*conditions))
    if criteria.tenant_id is not None:
        stmt = stmt.where(AuditLog.tenant_id == criteria.tenant_id)
    rows = (await db.execute(stmt.order_by(AuditLog.created_at).limit(MAX_ENTRIES))).scalars().all()
    return [
        AuditEntry(
            created_at=row.created_at,
            tenant_id=row.tenant_id,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            user_id=row.user_id,
            ip=row.ip,
            metadata=dict(row.metadata_ or {}),
        )
        for row in rows
    ]


async def lookup(db: AsyncSession, criteria: LookupCriteria) -> LookupResult:
    """Línea de tiempo de la persona o del fichero en todos los tenants (o en uno)."""
    if not criteria.labels():
        raise ValueError("Indica al menos un dato: email, nombre, fichero o SHA-256.")
    meta_conditions = _metadata_conditions(criteria)
    about = await _entries(db, criteria, meta_conditions)
    user_ids = await _users_by_identity(db, criteria)
    user_ids |= await _users_from_memberships(db, about)

    conditions = list(meta_conditions)
    if user_ids:
        conditions.append(AuditLog.user_id.in_(user_ids))
    result = LookupResult(user_ids=user_ids, entries=await _entries(db, criteria, conditions))
    await _record_lookup(db, criteria, result)
    return result


async def _record_lookup(db: AsyncSession, criteria: LookupCriteria, result: LookupResult) -> None:
    """Deja constancia del rastreo en cada tenant con resultados (sin el dato buscado)."""
    counts: dict[UUID, int] = {}
    for entry in result.entries:
        counts[entry.tenant_id] = counts.get(entry.tenant_id, 0) + 1
    for tenant_id, count in counts.items():
        await set_tenant_context(db, str(tenant_id))
        await audit_service.log_action(
            db,
            tenant_id=tenant_id,
            user_id=None,
            action=ACTION_AUDIT_LOOKUP,
            resource_type=RESOURCE_TENANT,
            resource_id=tenant_id,
            metadata={"criteria": criteria.labels(), "entries": count},
        )


def entry_summary(entry: AuditEntry) -> str:
    """Una línea legible por entrada para la salida del script."""
    meta = ", ".join(f"{key}={value}" for key, value in sorted(entry.metadata.items()))
    return (
        f"{entry.created_at:%Y-%m-%d %H:%M:%S}  tenant={entry.tenant_id}  {entry.action}  "
        f"{entry.resource_type}={entry.resource_id}  user={entry.user_id}  ip={entry.ip}  "
        f"{meta}"
    )
