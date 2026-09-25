"""Diagnostica si knowledge_rrf_k PUEDE afectar al ranking para el eval set actual.

RRF calcula score(chunk) = sum(1 / (k + rank_i)) sobre las listas en las que
aparece. Si un chunk aparece en una sola lista (dense O sparse, no ambas), su
posición relativa frente a otro chunk también "single-list" es:

    1/(k + rank_a) > 1/(k + rank_b)  <=>  rank_a < rank_b

...independiente de k. El valor de k SOLO puede cambiar el orden cuando hay
chunks que aparecen en AMBAS listas (su score combina dos términos que se
reescalan de forma distinta según k) compitiendo con chunks de una sola lista.

Por tanto: si para las queries del eval no hay solape entre el top dense y el
top sparse, variar knowledge_rrf_k entre 10/60/120 NUNCA puede cambiar
recall_at_5/recall_at_10 — no es un problema de caché ni de configuración,
es una propiedad matemática de los datos actuales.

Uso:
    infisical run -- uv run python scripts/rrf_overlap_diagnosis.py <tenant_uuid>
"""

# ruff: noqa: ASYNC240

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DATASET = Path("app/evals/datasets/knowledge_retrieval_v1.json")


async def _diagnose_case(db, tenant_id: uuid.UUID, case: dict) -> None:
    from app.llm.client import get_llm_client
    from app.schemas.knowledge_search import KnowledgeSearchFilters
    from app.services import knowledge_search_service as svc

    query = str(case["query"])
    filters = KnowledgeSearchFilters(top_k=10)
    settings_module = __import__("app.config", fromlist=["get_settings"])
    settings = settings_module.get_settings()

    client = get_llm_client()
    query_vector = (await client.embed(texts=[query], tenant_id=tenant_id, db=db))[0]
    filter_sql, filter_params = svc._build_filters_sql(filters)

    dense = await svc._dense_search(
        db,
        tenant_id=tenant_id,
        query_vector=query_vector,
        filter_sql=filter_sql,
        filter_params=filter_params,
        limit=settings.knowledge_dense_candidates,
    )
    sparse = await svc._sparse_search(
        db,
        tenant_id=tenant_id,
        query=query,
        filter_sql=filter_sql,
        filter_params=filter_params,
        limit=settings.knowledge_sparse_candidates,
    )

    dense_rank = {row["chunk_id"]: row["rank"] for row in dense}
    sparse_rank = {row["chunk_id"]: row["rank"] for row in sparse}
    overlap = set(dense_rank) & set(sparse_rank)

    print(f"\n=== case {case['id']!r}: {query[:80]!r}")
    print(f"    dense candidates={len(dense_rank)}  sparse candidates={len(sparse_rank)}")
    print(f"    chunks en AMBAS listas (único caso donde k puede importar): {len(overlap)}")
    for cid in sorted(overlap, key=lambda c: dense_rank[c]):
        print(
            f"      chunk {cid[:8]}…  dense_rank={dense_rank[cid]}  sparse_rank={sparse_rank[cid]}"
        )
    if not overlap:
        print("    -> SIN solape: el orden RRF para esta query es matemáticamente")
        print("       INDEPENDIENTE de knowledge_rrf_k (para cualquier k > 0).")


async def main(tenant_id: uuid.UUID) -> None:
    from app.core.db import session_factory_for_worker

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    cases = [c for c in dataset.get("cases", []) if not c.get("skip_live")]

    async with session_factory_for_worker(tenant_id) as db:
        for case in cases:
            await _diagnose_case(db, tenant_id, case)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.stderr.write("Uso: uv run python scripts/rrf_overlap_diagnosis.py <tenant_uuid>\n")
        raise SystemExit(1)
    asyncio.run(main(uuid.UUID(sys.argv[1])))
