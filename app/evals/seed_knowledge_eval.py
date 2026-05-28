"""Siembra datos sintéticos de conocimiento en BD para ejecutar el eval de retrieval.

Los chunks usan embeddings unitarios (no reales) porque el eval mide Recall@5
a través de búsqueda híbrida; BM25 es suficiente para las queries del dataset
cuando el contenido contiene las subcadenas esperadas.

Uso:
    infisical run -- uv run python -m app.evals.seed_knowledge_eval <tenant_uuid>
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog

from app.core.db import session_factory_for_worker, set_tenant_context
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentKind,
    KnowledgeDocumentStatus,
)

logger = structlog.get_logger(__name__)

_DIM = 512


def _unit(index: int) -> list[float]:
    v = [0.0] * _DIM
    v[index % _DIM] = 1.0
    return v


_SEED_DOCS: list[tuple[dict[str, object], str]] = [
    # (document kwargs, chunk content)
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


async def seed(tenant_id: UUID) -> None:
    now = datetime.now(UTC)
    async with session_factory_for_worker(tenant_id) as db:
        await set_tenant_context(db, str(tenant_id))
        for i, (doc_kwargs, content) in enumerate(_SEED_DOCS):
            doc = KnowledgeDocument(
                tenant_id=tenant_id,
                status=KnowledgeDocumentStatus.ready,
                chunk_count=1,
                file_size_bytes=len(content),
                source_file_key=f"tenants/{tenant_id}/knowledge/eval_seed_{i}.txt",
                ingested_at=now,
                **doc_kwargs,
            )
            db.add(doc)
            await db.flush()
            chunk = KnowledgeChunk(
                id=uuid4(),
                tenant_id=tenant_id,
                document_id=doc.id,
                content=content,
                embedding=_unit(i),
                position=0,
            )
            db.add(chunk)

        await db.commit()
        logger.info("knowledge_eval.seed_done", tenant_id=str(tenant_id), docs=len(_SEED_DOCS))
        print(f"Sembrados {len(_SEED_DOCS)} documentos para tenant {tenant_id}")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("Uso: infisical run -- uv run python -m app.evals.seed_knowledge_eval <tenant_uuid>")
        sys.exit(1)
    tenant_id = UUID(args[0])
    asyncio.run(seed(tenant_id))


if __name__ == "__main__":
    main()
