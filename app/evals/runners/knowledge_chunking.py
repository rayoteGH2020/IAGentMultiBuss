"""Runner del eval set de chunking de documentos de conocimiento (módulo 2).

Mide la calidad del chunking sobre textos de referencia sin necesitar Postgres
ni Voyage AI: solo evalúa ``chunk_text`` de ``app.core.text_chunking``.

Métricas:
  - ``chunk_count``: número de chunks generados por documento.
  - ``avg_chunk_size_chars``: tamaño medio de chunk en caracteres.
  - ``min_chunks_pass``: ¿el documento generó al menos el mínimo esperado?
  - ``max_chunks_pass``: ¿el documento no superó el máximo esperado?

Uso:
    uv run python -m app.evals.runners.knowledge_chunking
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from app.config import get_settings
from app.core.text_chunking import TooManyChunksError, chunk_text

DATASET = Path(__file__).parent.parent / "datasets" / "knowledge_chunking_v1.json"
RESULTS_DIR = Path(__file__).parent.parent / "results"


@dataclass
class CaseResult:
    case_id: str
    chunk_count: int
    avg_chunk_size_chars: float
    min_chunks_pass: bool
    max_chunks_pass: bool
    latency_ms: int
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and self.min_chunks_pass and self.max_chunks_pass


def _run_case(case: dict[str, Any]) -> CaseResult:
    case_id = case["id"]
    text: str = case.get("text", "")
    expected: dict[str, int] = case.get("expected", {})
    min_chunks = expected.get("min_chunks", 1)
    max_chunks = expected.get("max_chunks", 500)

    t0 = time.perf_counter()
    try:
        s = get_settings()
        chunks = chunk_text(
            text,
            target_tokens=s.knowledge_chunk_target_tokens,
            overlap_tokens=s.knowledge_chunk_overlap_tokens,
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        chunk_count = len(chunks)
        avg_size = mean(len(c.text) for c in chunks) if chunks else 0.0
        return CaseResult(
            case_id=case_id,
            chunk_count=chunk_count,
            avg_chunk_size_chars=avg_size,
            min_chunks_pass=chunk_count >= min_chunks,
            max_chunks_pass=chunk_count <= max_chunks,
            latency_ms=latency_ms,
        )
    except TooManyChunksError as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return CaseResult(
            case_id=case_id,
            chunk_count=exc.count,
            avg_chunk_size_chars=0.0,
            min_chunks_pass=False,
            max_chunks_pass=False,
            latency_ms=latency_ms,
            error=str(exc),
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return CaseResult(
            case_id=case_id,
            chunk_count=0,
            avg_chunk_size_chars=0.0,
            min_chunks_pass=False,
            max_chunks_pass=False,
            latency_ms=latency_ms,
            error=str(exc)[:300],
        )


def _summary(results: list[CaseResult]) -> dict[str, Any]:
    passed = [r for r in results if r.passed]
    avg_sizes = [r.avg_chunk_size_chars for r in results if r.error is None and r.chunk_count > 0]
    return {
        "total_cases": len(results),
        "passed_cases": len(passed),
        "pass_rate": len(passed) / max(len(results), 1),
        "global_avg_chunk_size_chars": mean(avg_sizes) if avg_sizes else None,
        "cases": [
            {
                "id": r.case_id,
                "passed": r.passed,
                "chunk_count": r.chunk_count,
                "avg_chunk_size_chars": round(r.avg_chunk_size_chars, 1),
                "min_chunks_pass": r.min_chunks_pass,
                "max_chunks_pass": r.max_chunks_pass,
                "latency_ms": r.latency_ms,
                "error": r.error,
            }
            for r in results
        ],
    }


def run_evals() -> dict[str, Any]:
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    results = [_run_case(case) for case in dataset.get("cases", [])]
    return _summary(results)


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    summary = run_evals()
    out = RESULTS_DIR / f"knowledge_chunking_v1_{int(time.time())}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    keys = ("total_cases", "passed_cases", "pass_rate", "global_avg_chunk_size_chars")
    sys.stdout.write(json.dumps({k: summary.get(k) for k in keys}, indent=2) + "\n")
    sys.stdout.write(f"Detalle: {out}\n")


if __name__ == "__main__":
    main()
