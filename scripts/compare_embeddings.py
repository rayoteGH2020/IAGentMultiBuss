"""Compara voyage-3-lite vs voyage-3 midiendo similitud coseno en memoria.

No requiere Postgres ni cambios de esquema: embebe pares (query, chunk)
del dataset de retrieval y calcula la similitud directamente en Python.

Uso:
    infisical run -- uv run python scripts/compare_embeddings.py
"""

# ruff: noqa: E402, ASYNC240

import asyncio
import json
import math
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import voyageai

DATASET = Path("app/evals/datasets/knowledge_retrieval_v1.json")
MODELS = ["voyage-3-lite", "voyage-3"]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


async def score_model(
    client: voyageai.AsyncClient,
    model: str,
    pairs: list[tuple[str, str]],
) -> dict:
    queries = [p[0] for p in pairs]
    chunks = [p[1] for p in pairs]

    t0 = time.perf_counter()
    r_q = await client.embed(queries, model=model, input_type="query")
    r_c = await client.embed(chunks, model=model, input_type="document")
    elapsed = (time.perf_counter() - t0) * 1000

    sims = [cosine(q, c) for q, c in zip(r_q.embeddings, r_c.embeddings, strict=True)]
    return {
        "model": model,
        "mean_sim": round(sum(sims) / len(sims), 4),
        "min_sim": round(min(sims), 4),
        "latency_ms": round(elapsed),
        "tokens": r_q.total_tokens + r_c.total_tokens,
        "dims": len(r_q.embeddings[0]),
    }


async def main() -> None:
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    pairs = [
        (case["query"], case["relevant_content_substrings"][0])
        for case in data["cases"]
        if case.get("relevant_content_substrings")
    ]
    print(f"Pares evaluados: {len(pairs)}\n")

    client = voyageai.AsyncClient(api_key=os.environ["VOYAGE_API_KEY"])

    results = []
    for model in MODELS:
        result = await score_model(client, model, pairs)
        results.append(result)
        print(
            f"[{result['model']:20}]  dims={result['dims']}  "
            f"mean_sim={result['mean_sim']}  min_sim={result['min_sim']}  "
            f"latency={result['latency_ms']}ms  tokens={result['tokens']}"
        )

    print()
    if len(results) == 2:
        delta = round(results[1]["mean_sim"] - results[0]["mean_sim"], 4)
        conclusion = (
            "voyage-3 mejora notablemente - valorar migración de BD."
            if delta > 0.02
            else "Diferencia pequeña - voyage-3-lite suficiente para este dominio."
        )
        print(f"Delta mean_sim (voyage-3 - voyage-3-lite): {delta:+.4f}  ->  {conclusion}")


asyncio.run(main())
