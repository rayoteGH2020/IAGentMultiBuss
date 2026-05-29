"""Servicio de búsqueda híbrida sobre la base de conocimiento RAG (Paso 19).

Combina búsqueda vectorial densa (HNSW cosine, voyage-3-lite 512d) y búsqueda de
texto dispersa (BM25 via tsvector + índice GIN) mediante Reciprocal Rank Fusion.

Pipeline de una búsqueda:
  1. Valida query y clipa top_k al techo configurado.
  2. Rate-limit por tenant: 120 req/min (Redis, ventana deslizante por minuto UTC).
  3. Embebe el query con LLMClient.embed() — registra en llm_calls automáticamente.
  4. Dense query (HNSW) + sparse query (BM25) en secuencia (misma AsyncSession).
  5. Reciprocal Rank Fusion: score = Σ 1/(k + rank_i) con k=60.
  6. Ordena por RRF desc, toma top_k.
  7. Traza Langfuse (span 'knowledge_search') + audit_log (query_hash SHA-256).

Funciones exportadas:
  search()              — búsqueda híbrida principal.
  get_chunk_by_id()     — recupera chunk concreto por UUID (tool get_knowledge_chunk).
  list_ready_documents() — documentos en estado 'ready' (tool list_knowledge_sources).
"""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from langfuse.types import TraceContext
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import RateLimitError, ValidationError
from app.core.text_normalization import normalize_search_text
from app.llm.tracing import get_langfuse
from app.models.knowledge import KnowledgeDocumentKind, KnowledgeDocumentStatus
from app.schemas.knowledge_search import (
    KnowledgeChunkRef,
    KnowledgeSearchFilters,
    KnowledgeSearchResult,
    KnowledgeSourceRef,
)
from app.services import audit_service

if TYPE_CHECKING:
    from app.llm.client import LLMClient

logger = structlog.get_logger(__name__)

# Máx. caracteres de query para embed; evita tokenización innecesariamente larga.
_MAX_QUERY_CHARS: int = 1000
# Clave Redis para rate-limit: ventana deslizante de 1 minuto por tenant.
_SEARCH_RATE_KEY_TPL = "rate:knowledge_search:{tenant_id}:{minute}"
# TTL de la clave: 2 min para que expire tras el primer minuto inactivo.
_SEARCH_RATE_TTL_SECONDS: int = 120
# Constantes de audit_log.
ACTION_KNOWLEDGE_SEARCH = "knowledge.search"
RESOURCE_KNOWLEDGE = "knowledge"


# ---------------------------------------------------------------------------
# Función pública principal
# ---------------------------------------------------------------------------


async def search(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    query: str,
    filters: KnowledgeSearchFilters,
    llm_client: LLMClient,
    redis: Any | None = None,
    langfuse_parent_trace_id: str | None = None,
) -> KnowledgeSearchResult:
    """Búsqueda híbrida: dense (HNSW) + sparse (BM25) + Reciprocal Rank Fusion.

    Args:
        db: AsyncSession con RLS ya configurado para el tenant.
        tenant_id: Tenant activo en el contexto de la petición.
        query: Pregunta en lenguaje natural (máx. 1000 chars).
        filters: Filtros opcionales de categoría, documentos y top_k.
        llm_client: Cliente LLM para obtener el embedding del query.
        redis: Cliente redis.asyncio para rate-limit; si es None se omite el check
               (útil en tests unitarios donde no hay Redis disponible).
        langfuse_parent_trace_id: Si se proporciona (p. ej. desde ``chat_rag_turn``),
            la búsqueda se registra como sub-span en esa traza.

    Returns:
        KnowledgeSearchResult con los chunks más relevantes y métricas de latencia.

    Raises:
        ValidationError: Query vacía o superior a 1000 caracteres.
        RateLimitError: El tenant supera 120 búsquedas/minuto.
    """
    settings = get_settings()
    t_start = time.monotonic()

    # 1. Validación básica
    query = query.strip()
    if not query:
        raise ValidationError("La query de búsqueda no puede estar vacía.")
    if len(query) > _MAX_QUERY_CHARS:
        raise ValidationError(
            f"La query de búsqueda no puede superar {_MAX_QUERY_CHARS} caracteres "
            f"(recibida: {len(query)})."
        )

    # 2. Clipa top_k al techo configurado
    top_k = min(filters.top_k, settings.knowledge_max_top_k)

    # 3. Rate-limit por tenant (sliding window de 1 minuto)
    if redis is not None:
        await _check_search_rate(
            redis,
            tenant_id=tenant_id,
            limit=settings.knowledge_search_rpm_limit,
        )

    # 4. Inicia traza Langfuse (sub-span de chat_rag_turn si hay parent)
    langfuse = get_langfuse()
    if langfuse_parent_trace_id:
        trace_ctx = TraceContext(trace_id=langfuse_parent_trace_id)
    else:
        trace_uuid = langfuse.create_trace_id()
        trace_ctx = TraceContext(trace_id=str(trace_uuid))
    obs = langfuse.start_observation(
        trace_context=trace_ctx,
        name="search_knowledge",
        as_type="span",
        metadata={"tenant_id": str(tenant_id)},
        input={"query": query[:200], "top_k": top_k},
    )

    dense_rows: list[dict[str, Any]] = []
    sparse_rows: list[dict[str, Any]] = []

    try:
        # 5. Embebe el query (registra en llm_calls + Langfuse automáticamente)
        embedding_vecs = await llm_client.embed(
            texts=[query],
            tenant_id=tenant_id,
            db=db,
        )
        query_vector: list[float] = embedding_vecs[0]

        # 6. Dense + sparse en paralelo
        filter_sql, filter_params = _build_filters_sql(filters)

        # Secuencial: AsyncSession (asyncpg) no permite execute concurrente en la misma sesión.
        dense_rows = await _dense_search(
            db,
            tenant_id=tenant_id,
            query_vector=query_vector,
            filter_sql=filter_sql,
            filter_params=filter_params,
            limit=settings.knowledge_dense_candidates,
        )
        sparse_rows = await _sparse_search(
            db,
            tenant_id=tenant_id,
            query=query,
            filter_sql=filter_sql,
            filter_params=filter_params,
            limit=settings.knowledge_sparse_candidates,
        )

        # 7. Reciprocal Rank Fusion
        merged = _rrf_merge(dense_rows, sparse_rows, k=settings.knowledge_rrf_k)
        total_found = len(merged)

        # 8. Toma top_k
        top_rows = merged[:top_k]

        # 9. Construye la lista de chunks de salida
        chunks = [
            KnowledgeChunkRef(
                id=UUID(row["chunk_id"]),
                document_id=UUID(row["document_id"]),
                document_name=row["document_name"],
                kind=KnowledgeDocumentKind(row["kind"]),
                position=int(row["position"]),
                content=row["content"],
                context=row.get("context"),
                metadata=row.get("metadata") or {},
                score=float(row["rrf_score"]),
            )
            for row in top_rows
        ]

        latency_ms = int((time.monotonic() - t_start) * 1000)

        # 10. Cierra span Langfuse con métricas
        obs.update(
            output={
                "dense_candidates": len(dense_rows),
                "sparse_candidates": len(sparse_rows),
                "rrf_merged_count": total_found,
                "final_count": len(chunks),
                "latency_ms": latency_ms,
            },
        )
        obs.end()
        langfuse.flush()

        # 11. Audit log (query_hash SHA-256, nunca el texto plano)
        query_hash = hashlib.sha256(query.encode()).hexdigest()
        await audit_service.log_action(
            db,
            tenant_id=tenant_id,
            user_id=None,
            action=ACTION_KNOWLEDGE_SEARCH,
            resource_type=RESOURCE_KNOWLEDGE,
            metadata={
                "query_hash": query_hash,
                "top_k": top_k,
                "chunks_found": len(chunks),
                "latency_ms": latency_ms,
            },
        )

        logger.info(
            "knowledge.search.ok",
            tenant_id=str(tenant_id),
            dense=len(dense_rows),
            sparse=len(sparse_rows),
            merged=total_found,
            returned=len(chunks),
            latency_ms=latency_ms,
        )

        return KnowledgeSearchResult(
            query=query,
            total_found=total_found,
            chunks=chunks,
            latency_ms=latency_ms,
        )

    except Exception:
        obs.end()
        raise


# ---------------------------------------------------------------------------
# Funciones auxiliares de búsqueda
# ---------------------------------------------------------------------------


def _build_filters_sql(
    filters: KnowledgeSearchFilters,
) -> tuple[str, dict[str, Any]]:
    """Construye fragmento SQL WHERE y dict de params para los filtros opcionales.

    Usa parámetros numerados (:kind_0, :kind_1, :doc_id_0 …) para evitar
    f-strings con datos de usuario. Los valores de kind son enums validados;
    los document_ids son UUIDs validados por Pydantic.

    Returns:
        Tupla (sql_fragment, params_dict). El fragmento ya incluye 'AND'.
    """
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if filters.kind:
        for i, k in enumerate(filters.kind):
            params[f"kind_{i}"] = k.value
        placeholders = ", ".join(f":kind_{i}" for i in range(len(filters.kind)))
        clauses.append(f"AND d.kind IN ({placeholders})")

    if filters.document_ids:
        for i, did in enumerate(filters.document_ids):
            params[f"doc_id_{i}"] = str(did)
        placeholders = ", ".join(
            f"CAST(:doc_id_{i} AS uuid)" for i in range(len(filters.document_ids))
        )
        clauses.append(f"AND c.document_id IN ({placeholders})")

    return " ".join(clauses), params


async def _dense_search(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    query_vector: list[float],
    filter_sql: str,
    filter_params: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    """Búsqueda vectorial cosine HNSW. Devuelve filas con rank y cosine_score.

    El vector se pasa como cadena '[v1,v2,...,vN]' y el cast ::vector(512)
    en SQL lo parsea. No incluye el campo embedding en la proyección.
    """
    # Serializa el vector como literal pgvector: '[v1,v2,...,vN]'
    vec_str = "[" + ",".join(str(v) for v in query_vector) + "]"

    params: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "vec": vec_str,
        "limit_val": limit,
        **filter_params,
    }

    # filter_sql solo contiene nombres de parámetros (:kind_0, etc.) generados
    # por código, no por entrada del usuario.
    sql = text(
        f"""
SELECT
    c.id::text                                              AS chunk_id,
    c.document_id::text                                     AS document_id,
    d.name                                                  AS document_name,
    d.kind::text                                            AS kind,
    c.position,
    c.content,
    c.context,
    c.metadata,
    1 - (c.embedding <=> CAST(:vec AS vector(512)))          AS cosine_score,
    ROW_NUMBER() OVER (
        ORDER BY c.embedding <=> CAST(:vec AS vector(512))
    )                                                       AS rank
FROM knowledge_chunks c
JOIN knowledge_documents d ON d.id = c.document_id
WHERE c.tenant_id = CAST(:tenant_id AS uuid)
  AND d.status = 'ready'
  {filter_sql}
ORDER BY c.embedding <=> CAST(:vec AS vector(512))
LIMIT :limit_val
"""
    )

    result = await db.execute(sql, params)
    return [dict(row) for row in result.mappings().all()]


async def _sparse_search(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    query: str,
    filter_sql: str,
    filter_params: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    """Búsqueda BM25 via tsvector GIN. Devuelve filas con rank y bm25_score.

    Normaliza el query (strip acentos + lowercase) antes de pasarlo a
    plainto_tsquery. Si el query resulta vacío tras normalización (stopwords
    o caracteres especiales), devuelve lista vacía sin hacer round-trip a BD.
    """
    normalized_q = normalize_search_text(query)
    if not normalized_q:
        logger.debug(
            "knowledge.search.sparse.empty_normalized_query",
            tenant_id=str(tenant_id),
        )
        return []

    params: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "q": normalized_q,
        "limit_val": limit,
        **filter_params,
    }

    sql = text(
        f"""
SELECT
    c.id::text                                              AS chunk_id,
    c.document_id::text                                     AS document_id,
    d.name                                                  AS document_name,
    d.kind::text                                            AS kind,
    c.position,
    c.content,
    c.context,
    c.metadata,
    ts_rank_cd(c.search_vector, plainto_tsquery('spanish', :q)) AS bm25_score,
    ROW_NUMBER() OVER (
        ORDER BY ts_rank_cd(
            c.search_vector, plainto_tsquery('spanish', :q)
        ) DESC
    )                                                       AS rank
FROM knowledge_chunks c
JOIN knowledge_documents d ON d.id = c.document_id
WHERE c.tenant_id = CAST(:tenant_id AS uuid)
  AND d.status = 'ready'
  AND c.search_vector @@ plainto_tsquery('spanish', :q)
  {filter_sql}
ORDER BY bm25_score DESC
LIMIT :limit_val
"""
    )

    result = await db.execute(sql, params)
    return [dict(row) for row in result.mappings().all()]


def _rrf_merge(
    dense_rows: list[dict[str, Any]],
    sparse_rows: list[dict[str, Any]],
    *,
    k: int,
) -> list[dict[str, Any]]:
    """Fusiona dos listas rankeadas con Reciprocal Rank Fusion.

    Fórmula:  score_rrf(chunk) = Σ_i  1 / (k + rank_i)

    Si un chunk aparece en ambas listas sus contribuciones se suman.
    Si solo aparece en una lista se puntúa solo con esa contribución.
    El resultado se ordena por score_rrf descendente.

    Args:
        dense_rows: Resultados HNSW; cada fila incluye 'rank' (int ≥ 1).
        sparse_rows: Resultados BM25; cada fila incluye 'rank' (int ≥ 1).
        k: Constante de suavizado RRF (estándar: 60).

    Returns:
        Lista de dicts con campo 'rrf_score' añadido, ordenados desc, sin duplicados.
    """
    scores: dict[str, float] = {}
    chunk_data: dict[str, dict[str, Any]] = {}

    for row in dense_rows:
        cid: str = row["chunk_id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + int(row["rank"]))
        if cid not in chunk_data:
            chunk_data[cid] = row

    for row in sparse_rows:
        cid = row["chunk_id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + int(row["rank"]))
        if cid not in chunk_data:
            chunk_data[cid] = row

    # Ordena por score RRF desc y añade el score al dict de cada chunk
    merged: list[dict[str, Any]] = []
    for cid, rrf_score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        row = dict(chunk_data[cid])
        row["rrf_score"] = rrf_score
        merged.append(row)

    return merged


# ---------------------------------------------------------------------------
# Normalización de scores para canales externos
# ---------------------------------------------------------------------------


def normalize_rrf_score(rrf_score: float, *, rrf_k: int) -> float:
    """Convierte un score RRF a escala 0-1 compatible con ``confidence_threshold``.

    El score RRF máximo teórico para una lista es ``1/(rrf_k + 1)``.
    Multiplicar por ``(rrf_k + 1)`` lleva el rango ``[0, 1/(k+1)]`` → ``[0, 1]``.
    Si el chunk aparece en ambas listas (dense + sparse) el score se clampea a 1.0.

    Ejemplos con rrf_k=60 (max teórico por lista = 1/61 ≈ 0.0164):
      rank 1  → 0.0164 → 1.000  (resultado perfecto)
      rank 5  → 0.0154 → 0.938
      rank 10 → 0.0143 → 0.871
      rank 30 → 0.0111 → 0.678
      rank 60 → 0.0083 → 0.508

    Usado por ``channel_chat_service`` para calcular ``confidence`` antes de
    comparar con ``ChannelIntegration.confidence_threshold`` (escala 0-1).
    """
    if rrf_k <= 0:
        return 0.0
    max_single = 1.0 / (rrf_k + 1)
    return min(rrf_score / max_single, 1.0)


# ---------------------------------------------------------------------------
# Rate-limit
# ---------------------------------------------------------------------------


async def _check_search_rate(
    redis: Any,
    *,
    tenant_id: UUID,
    limit: int,
) -> None:
    """Ventana deslizante de 1 minuto por tenant via Redis INCR + EXPIRE.

    Patrón: INCR especulativo; si supera el límite se revierte con DECR.
    TTL de 2 minutos asegura limpieza tras el último incremento del minuto.

    Raises:
        RateLimitError: Si el contador del minuto actual supera `limit`.
    """
    minute_str = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    key = _SEARCH_RATE_KEY_TPL.format(tenant_id=tenant_id, minute=minute_str)

    count = int(await redis.incr(key))
    # Establece TTL solo en el primer incremento para no reset el contador.
    if count == 1:
        await redis.expire(key, _SEARCH_RATE_TTL_SECONDS)

    if count > limit:
        # Revierte el incremento especulativo.
        await redis.decr(key)
        logger.warning(
            "knowledge.search.rate_limit",
            tenant_id=str(tenant_id),
            count=count,
            limit=limit,
        )
        raise RateLimitError(
            f"Límite de búsquedas alcanzado ({limit}/minuto). Inténtalo de nuevo en unos segundos."
        )


# ---------------------------------------------------------------------------
# Funciones auxiliares para las tools de conocimiento (Fase C)
# ---------------------------------------------------------------------------


async def get_chunk_by_id(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    chunk_id: UUID,
) -> KnowledgeChunkRef | None:
    """Recupera un chunk concreto por su UUID.

    RLS aplica aislamiento de tenant implícitamente; el WHERE explícito es
    defensa en profundidad según AGENTS.md §7.
    Devuelve None si el chunk no existe o no pertenece al tenant.
    El campo embedding está excluido de la proyección (invariante del módulo).
    """
    sql = text("""
SELECT
    c.id::text          AS chunk_id,
    c.document_id::text AS document_id,
    d.name              AS document_name,
    d.kind::text        AS kind,
    c.position,
    c.content,
    c.context,
    c.metadata
FROM knowledge_chunks c
JOIN knowledge_documents d ON d.id = c.document_id
WHERE c.id          = CAST(:chunk_id AS uuid)
  AND c.tenant_id   = CAST(:tenant_id AS uuid)
""")
    result = await db.execute(
        sql,
        {"chunk_id": str(chunk_id), "tenant_id": str(tenant_id)},
    )
    row = result.mappings().first()
    if row is None:
        return None

    return KnowledgeChunkRef(
        id=UUID(row["chunk_id"]),
        document_id=UUID(row["document_id"]),
        document_name=row["document_name"],
        kind=KnowledgeDocumentKind(row["kind"]),
        position=int(row["position"]),
        content=row["content"],
        context=row.get("context"),
        metadata=row.get("metadata") or {},
        # score = 0.0: no hay score de relevancia en recuperación directa por ID.
        score=0.0,
    )


async def list_ready_documents(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    kind_filter: list[KnowledgeDocumentKind] | None = None,
) -> list[KnowledgeSourceRef]:
    """Lista documentos en estado 'ready' para la tool list_knowledge_sources.

    Devuelve la vista ligera KnowledgeSourceRef (sin source_file_key, tenant_id
    ni otros campos de infraestructura) para que el LLM decida dónde buscar.

    Args:
        db: AsyncSession con RLS configurado.
        tenant_id: Tenant activo.
        kind_filter: Si se especifica, filtra solo las categorías indicadas.

    Returns:
        Lista de KnowledgeSourceRef ordenada por ingested_at desc.
    """
    params: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "status": KnowledgeDocumentStatus.ready.value,
    }

    kind_clause = ""
    if kind_filter:
        for i, k in enumerate(kind_filter):
            params[f"kind_{i}"] = k.value
        placeholders = ", ".join(f":kind_{i}" for i in range(len(kind_filter)))
        kind_clause = f"AND kind IN ({placeholders})"

    sql = text(
        f"""
SELECT
    id::text    AS doc_id,
    name,
    kind::text  AS kind,
    chunk_count,
    ingested_at
FROM knowledge_documents
WHERE tenant_id = CAST(:tenant_id AS uuid)
  AND status    = :status
  {kind_clause}
ORDER BY ingested_at DESC NULLS LAST, created_at DESC
"""
    )

    result = await db.execute(sql, params)
    rows = result.mappings().all()

    return [
        KnowledgeSourceRef(
            id=UUID(row["doc_id"]),
            name=row["name"],
            kind=KnowledgeDocumentKind(row["kind"]),
            chunk_count=int(row["chunk_count"]),
            ingested_at=row["ingested_at"].isoformat() if row["ingested_at"] else None,
        )
        for row in rows
    ]
