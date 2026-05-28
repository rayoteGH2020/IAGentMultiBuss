"""Siembra datos sintéticos de conocimiento en BD para ejecutar el eval de retrieval.

Los chunks usan embeddings unitarios (no reales) porque el eval mide Recall@5
a través de búsqueda híbrida; BM25 es suficiente para las queries del dataset
cuando el contenido contiene las subcadenas esperadas.

Requisitos:
  - Variables de entorno vía Infisical (``DATABASE_URL``, ``REDIS_URL``, etc.).
  - El ``tenant_uuid`` debe existir en la tabla ``tenants`` (id local de la app,
    no el org id de Clerk). Usa ``--list-tenants`` para ver IDs válidos.

Uso:
    infisical run -- uv run python -m app.evals.seed_knowledge_eval --list-tenants
    infisical run -- uv run python -m app.evals.seed_knowledge_eval <tenant_uuid>
    infisical run -- uv run python -m app.evals.seed_knowledge_eval <tenant_uuid> --no-replace
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from pydantic import ValidationError
from sqlalchemy import delete, select

from app.core.db import session_factory_for_worker, set_tenant_context
from app.models import Tenant
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentKind,
    KnowledgeDocumentStatus,
)

logger = structlog.get_logger(__name__)

_DIM = 512
_EVAL_SEED_PREFIX = "/knowledge/eval_seed_"


def _unit(index: int) -> list[float]:
    v = [0.0] * _DIM
    v[index % _DIM] = 1.0
    return v


_SEED_DOCS: list[tuple[dict[str, object], str]] = [
    (
        {
            "kind": KnowledgeDocumentKind.schedule,
            "name": "Horarios de atención",
            "original_filename": "horarios.md",
            "source_mime": "text/markdown",
        },
        "Horario de atención: Martes a viernes de 9:30 a 14:00 y de 16:30 a 20:30. "
        "Sábados de 9:00 a 14:00. Lunes cerrado.",
    ),
    (
        {
            "kind": KnowledgeDocumentKind.contract,
            "name": "Contrato servicios",
            "original_filename": "contrato.txt",
            "source_mime": "text/plain",
        },
        "Cláusula de pago: el importe mensual es 99 EUR. "
        "Referencia interna CODIGOCONTRATO4242 para facturación.",
    ),
    (
        {
            "kind": KnowledgeDocumentKind.policy,
            "name": "Política vestimenta",
            "original_filename": "policy.txt",
            "source_mime": "text/plain",
        },
        "Política interna: uniforme oscuro obligatorio en sala.",
    ),
    (
        {
            "kind": KnowledgeDocumentKind.faq,
            "name": "FAQ cancelaciones y pagos",
            "original_filename": "faq.md",
            "source_mime": "text/markdown",
        },
        "Cancelación de citas: debe realizarse con al menos 24 horas de antelación. "
        "Métodos de pago aceptados: efectivo, tarjeta bancaria y Bizum.",
    ),
    (
        {
            "kind": KnowledgeDocumentKind.services,
            "name": "Tarifas servicios",
            "original_filename": "servicios.md",
            "source_mime": "text/markdown",
        },
        "Servicios para caballero: Corte caballero desde 12 €. Barba desde 8 €. "
        "Corte con navaja 18 €.",
    ),
    (
        {
            "kind": KnowledgeDocumentKind.terms,
            "name": "Condiciones generales",
            "original_filename": "terminos.txt",
            "source_mime": "text/plain",
        },
        "Condiciones generales del servicio. Política de privacidad y tratamiento "
        "de datos personales conforme al RGPD.",
    ),
    (
        {
            "kind": KnowledgeDocumentKind.schedule,
            "name": "Horario término único",
            "original_filename": "clave.txt",
            "source_mime": "text/plain",
        },
        "Información de horario comercial con término único ZETATERMINO789 para pruebas BM25.",
    ),
]


def _settings_hint() -> str:
    return (
        "Faltan variables de entorno (APP_SECRET_KEY, DATABASE_URL, REDIS_URL). "
        "Ejecuta con Infisical:\n"
        "  infisical run -- uv run python -m app.evals.seed_knowledge_eval <tenant_uuid>"
    )


async def list_tenants() -> None:
    """Lista tenants locales para elegir un UUID válido."""
    try:
        from app.core.db import session_scope
    except ValidationError:
        print(_settings_hint(), file=sys.stderr)
        sys.exit(1)

    async with session_scope() as db:
        rows = (await db.execute(select(Tenant.id, Tenant.name, Tenant.clerk_org_id))).all()
    if not rows:
        print("No hay tenants en la BD. Crea uno con:")
        print("  infisical run -- uv run python scripts/seed_dev.py")
        return
    print("Tenants disponibles (usa el UUID de la columna id):")
    for tid, name, clerk_org in rows:
        clerk = clerk_org or "—"
        print(f"  {tid}  {name!r}  clerk_org_id={clerk}")


async def _tenant_exists(db: object, tenant_id: UUID) -> bool:
    result = await db.execute(select(Tenant.id).where(Tenant.id == tenant_id).limit(1))  # type: ignore[attr-defined]
    return result.scalar_one_or_none() is not None


async def _delete_eval_seed_docs(db: object, tenant_id: UUID) -> int:
    """Borra documentos de eval previos (CASCADE elimina chunks)."""
    pattern = f"tenants/{tenant_id}{_EVAL_SEED_PREFIX}%"
    stmt = delete(KnowledgeDocument).where(
        KnowledgeDocument.tenant_id == tenant_id,
        KnowledgeDocument.source_file_key.like(pattern),
    )
    result = await db.execute(stmt)  # type: ignore[attr-defined]
    return int(result.rowcount or 0)


async def seed(tenant_id: UUID, *, replace: bool = True) -> None:
    now = datetime.now(UTC)
    async with session_factory_for_worker(tenant_id) as db:
        if not await _tenant_exists(db, tenant_id):
            print(
                f"Error: no existe ningún tenant con id={tenant_id}.\n"
                "El UUID debe ser el de la tabla ``tenants`` (no el org id de Clerk).\n"
                "Lista tenants válidos con:\n"
                "  infisical run -- uv run python -m app.evals.seed_knowledge_eval --list-tenants",
                file=sys.stderr,
            )
            sys.exit(1)

        removed = 0
        if replace:
            removed = await _delete_eval_seed_docs(db, tenant_id)

        for i, (doc_kwargs, content) in enumerate(_SEED_DOCS):
            doc = KnowledgeDocument(
                tenant_id=tenant_id,
                status=KnowledgeDocumentStatus.ready,
                chunk_count=1,
                file_size_bytes=len(content.encode("utf-8")),
                source_file_key=f"tenants/{tenant_id}{_EVAL_SEED_PREFIX}{i}.txt",
                ingested_at=now,
                **doc_kwargs,
            )
            db.add(doc)
            await db.flush()
            chunk = KnowledgeChunk(
                id=uuid4(),
                tenant_id=tenant_id,
                content=content,
                embedding=_unit(i),
                position=0,
            )
            chunk.document_id = doc.id
            db.add(chunk)

        await db.commit()
        await set_tenant_context(db, str(tenant_id))

    logger.info(
        "knowledge_eval.seed_done",
        tenant_id=str(tenant_id),
        docs=len(_SEED_DOCS),
        removed_previous=removed,
    )
    msg = f"Sembrados {len(_SEED_DOCS)} documentos para tenant {tenant_id}"
    if replace and removed:
        msg += f" (reemplazados {removed} documentos eval previos)"
    print(msg)


def main() -> None:
    argv = [a for a in sys.argv[1:] if a]
    flags = {a for a in argv if a.startswith("--")}
    args = [a for a in argv if not a.startswith("--")]

    if "--list-tenants" in flags:
        asyncio.run(list_tenants())
        return

    if not args:
        print(__doc__ or "")
        print(
            "Uso: infisical run -- uv run python -m app.evals.seed_knowledge_eval <tenant_uuid>",
            file=sys.stderr,
        )
        sys.exit(1)

    replace = "--no-replace" not in flags
    try:
        tenant_id = UUID(args[0])
    except ValueError:
        print(f"UUID inválido: {args[0]!r}", file=sys.stderr)
        sys.exit(1)

    try:
        asyncio.run(seed(tenant_id, replace=replace))
    except ValidationError:
        print(_settings_hint(), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
