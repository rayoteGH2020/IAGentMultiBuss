"""Helpers para tests de integración de retrieval (Paso 19)."""

from __future__ import annotations

from datetime import UTC, datetime
from math import log2
from uuid import UUID

from app.core.db import set_tenant_context
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentKind,
    KnowledgeDocumentStatus,
)
from sqlalchemy.ext.asyncio import AsyncSession


def unit_vector(dim: int, index: int) -> list[float]:
    """Vector unitario 512d con 1.0 en la dimensión ``index``."""
    vec = [0.0] * dim
    vec[index] = 1.0
    return vec


def ndcg_at_k(relevant_id: UUID, ranked_ids: list[UUID], k: int) -> float:
    """nDCG@k para un único documento relevante (ganancia binaria)."""

    def dcg(ids: list[UUID]) -> float:
        score = 0.0
        for i, cid in enumerate(ids[:k], start=1):
            if cid == relevant_id:
                score += 1.0 / log2(i + 1)
        return score

    actual = dcg(ranked_ids)
    ideal = dcg([relevant_id])
    if ideal == 0.0:
        return 0.0
    return actual / ideal


class KnowledgeRetrievalSeed:
    """Documentos y chunks sintéticos para pruebas de búsqueda híbrida."""

    def __init__(
        self,
        *,
        schedule_chunk_id: UUID,
        contract_chunk_id: UUID,
        policy_chunk_id: UUID,
        noise_chunk_id: UUID,
        hit_chunk_id: UUID,
    ) -> None:
        self.schedule_chunk_id = schedule_chunk_id
        self.contract_chunk_id = contract_chunk_id
        self.policy_chunk_id = policy_chunk_id
        self.noise_chunk_id = noise_chunk_id
        self.hit_chunk_id = hit_chunk_id


async def seed_knowledge_retrieval(
    db: AsyncSession,
    *,
    tenant_id: UUID,
) -> KnowledgeRetrievalSeed:
    """Inserta 3 documentos ``ready`` con chunks controlados para retrieval."""
    from uuid import uuid4

    schedule_chunk_id = uuid4()
    contract_chunk_id = uuid4()
    policy_chunk_id = uuid4()
    noise_chunk_id = uuid4()
    hit_chunk_id = uuid4()

    now = datetime.now(UTC)
    docs_chunks: list[tuple[KnowledgeDocument, KnowledgeChunk]] = [
        (
            KnowledgeDocument(
                tenant_id=tenant_id,
                kind=KnowledgeDocumentKind.schedule,
                name="Horarios FAQ",
                original_filename="horarios.md",
                source_file_key=f"tenants/{tenant_id}/knowledge/horarios.md",
                source_mime="text/markdown",
                status=KnowledgeDocumentStatus.ready,
                chunk_count=1,
                file_size_bytes=500,
                ingested_at=now,
            ),
            KnowledgeChunk(
                id=schedule_chunk_id,
                tenant_id=tenant_id,
                content=(
                    "Horario de atención: Martes a viernes de 9:30 a 14:00 "
                    "y de 16:30 a 20:30. Sábados de 9:00 a 14:00."
                ),
                embedding=unit_vector(512, 0),
                position=0,
            ),
        ),
        (
            KnowledgeDocument(
                tenant_id=tenant_id,
                kind=KnowledgeDocumentKind.contract,
                name="Contrato servicios",
                original_filename="contrato.txt",
                source_file_key=f"tenants/{tenant_id}/knowledge/contrato.txt",
                source_mime="text/plain",
                status=KnowledgeDocumentStatus.ready,
                chunk_count=1,
                file_size_bytes=400,
                ingested_at=now,
            ),
            KnowledgeChunk(
                id=contract_chunk_id,
                tenant_id=tenant_id,
                content=(
                    "Cláusula de pago: el importe mensual es 99 EUR. "
                    "Referencia interna CODIGOCONTRATO4242 para facturación."
                ),
                embedding=unit_vector(512, 1),
                position=0,
            ),
        ),
        (
            KnowledgeDocument(
                tenant_id=tenant_id,
                kind=KnowledgeDocumentKind.policy,
                name="Política vestimenta",
                original_filename="policy.txt",
                source_file_key=f"tenants/{tenant_id}/knowledge/policy.txt",
                source_mime="text/plain",
                status=KnowledgeDocumentStatus.ready,
                chunk_count=1,
                file_size_bytes=300,
                ingested_at=now,
            ),
            KnowledgeChunk(
                id=policy_chunk_id,
                tenant_id=tenant_id,
                content="Política interna: uniforme oscuro obligatorio en sala.",
                embedding=unit_vector(512, 2),
                position=0,
            ),
        ),
        (
            KnowledgeDocument(
                tenant_id=tenant_id,
                kind=KnowledgeDocumentKind.faq,
                name="FAQ ruido denso",
                original_filename="ruido.txt",
                source_file_key=f"tenants/{tenant_id}/knowledge/ruido.txt",
                source_mime="text/plain",
                status=KnowledgeDocumentStatus.ready,
                chunk_count=1,
                file_size_bytes=200,
                ingested_at=now,
            ),
            KnowledgeChunk(
                id=noise_chunk_id,
                tenant_id=tenant_id,
                content="Texto genérico sin palabras clave relevantes para horarios.",
                embedding=unit_vector(512, 10),
                position=0,
            ),
        ),
        (
            KnowledgeDocument(
                tenant_id=tenant_id,
                kind=KnowledgeDocumentKind.schedule,
                name="FAQ horario clave",
                original_filename="clave.txt",
                source_file_key=f"tenants/{tenant_id}/knowledge/clave.txt",
                source_mime="text/plain",
                status=KnowledgeDocumentStatus.ready,
                chunk_count=1,
                file_size_bytes=250,
                ingested_at=now,
            ),
            KnowledgeChunk(
                id=hit_chunk_id,
                tenant_id=tenant_id,
                content=(
                    "Información de horario comercial con término único "
                    "ZETATERMINO789 para pruebas BM25."
                ),
                embedding=unit_vector(512, 50),
                position=0,
            ),
        ),
    ]

    for doc, chunk in docs_chunks:
        db.add(doc)
        await db.flush()
        chunk.document_id = doc.id
        db.add(chunk)

    await db.commit()
    # set_config(is_local=true) se revierte en commit; restaurar RLS para búsquedas.
    await set_tenant_context(db, str(tenant_id))

    return KnowledgeRetrievalSeed(
        schedule_chunk_id=schedule_chunk_id,
        contract_chunk_id=contract_chunk_id,
        policy_chunk_id=policy_chunk_id,
        noise_chunk_id=noise_chunk_id,
        hit_chunk_id=hit_chunk_id,
    )
