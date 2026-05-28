"""Verificación manual del Paso 19 — Búsqueda híbrida de conocimiento.

Ejecutar con:
    infisical run -- uv run python scripts/verify_knowledge_search.py

Opcionalmente pasar un tenant_id concreto:
    infisical run -- uv run python scripts/verify_knowledge_search.py <tenant_uuid>

Hace exactamente lo del checklist Paso19.md punto 4:
  - Localiza un tenant con documentos knowledge en estado 'ready'.
  - Lanza tres búsquedas de prueba (semántica, keyword exacta, híbrida).
  - Imprime score y snippet de cada resultado.
"""

from __future__ import annotations

import asyncio
import sys
from uuid import UUID

from sqlalchemy import select, text


async def main(tenant_id_arg: str | None = None) -> None:
    from app.core.db import session_factory_for_worker
    from app.llm.client import get_llm_client
    from app.models.knowledge import KnowledgeDocument, KnowledgeDocumentStatus
    from app.schemas.knowledge_search import KnowledgeSearchFilters
    from app.services import knowledge_search_service

    # ── 1. Resolver tenant_id ──────────────────────────────────────────────────
    if tenant_id_arg:
        tenant_id = UUID(tenant_id_arg)
        print(f"Usando tenant especificado: {tenant_id}\n")
    else:
        # Buscar el primer tenant con al menos un documento 'ready'
        from app.core.db import get_sessionmaker

        sm = get_sessionmaker()
        async with sm() as session:
            # Sin RLS activo: consulta a nivel de superusuario para encontrar tenant
            row = await session.execute(
                text("SELECT tenant_id FROM knowledge_documents " "WHERE status = 'ready' LIMIT 1")
            )
            result = row.first()
        if result is None:
            print(
                "No hay documentos en estado 'ready'. Sube y espera a que se indexen en /knowledge."
            )
            return
        tenant_id = UUID(str(result[0]))
        print(f"Tenant con documentos ready: {tenant_id}\n")

    # ── 2. Mostrar documentos disponibles ─────────────────────────────────────
    async with session_factory_for_worker(tenant_id) as db:
        docs_result = await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.status == KnowledgeDocumentStatus.ready,
            )
        )
        docs = docs_result.scalars().all()
        print(f"Documentos ready ({len(docs)}):")
        for d in docs:
            print(f"  [{d.kind}] {d.name}  — {d.chunk_count} chunks")
        print()

    if not docs:
        print("No hay documentos ready para buscar.")
        return

    llm = get_llm_client()

    # ── 3. Tres búsquedas de prueba ───────────────────────────────────────────
    queries = [
        ("horario de atención al cliente", None),
        ("cancelar cita 24 horas", None),
        ("precio servicios", None),
    ]

    for query, _kind_filter in queries:
        print(f"{'─'*60}")
        print(f"Query: «{query}»")
        filters = KnowledgeSearchFilters(top_k=5)
        try:
            async with session_factory_for_worker(tenant_id) as db:
                results = await knowledge_search_service.search(
                    db,
                    tenant_id=tenant_id,
                    query=query,
                    filters=filters,
                    llm_client=llm,
                    redis=None,  # sin rate-limit en script manual
                )
            print(
                f"  {results.total_found} candidatos tras RRF → top {len(results.chunks)} devueltos  ({results.latency_ms} ms)"
            )
            for i, chunk in enumerate(results.chunks, 1):
                snippet = chunk.content[:100].replace("\n", " ")
                print(f"  {i}. [{chunk.kind}] score={chunk.score:.4f}  {snippet!r}")
        except Exception as exc:
            print(f"  ERROR: {exc}")
        print()

    print(
        "Verificación completada. Comprueba también Langfuse y llm_calls para los spans de embedding."
    )


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(main(arg))
