"""Cliente Voyage AI para embeddings (módulo 2, Paso 18).

Encapsula la llamada a la API de Voyage: batching, normalización L2 y
devolución de resultados con conteo de tokens para observabilidad.

No se llama directamente desde services ni routes; se usa a través de
LLMClient.embed() que añade persistencia en llm_calls y trazas Langfuse.
"""

from __future__ import annotations

import math
from collections.abc import Generator
from dataclasses import dataclass

import voyageai

# Voyage recomienda lotes de hasta 128 textos, pero 64 es más seguro para
# documentos de texto largo (el límite real es de tokens, no de textos).
VOYAGE_BATCH_SIZE: int = 64


@dataclass
class EmbedBatchResult:
    """Resultado de un batch de embeddings con metadatos de observabilidad."""

    embeddings: list[list[float]]
    total_tokens: int


def _l2_normalize(vector: list[float]) -> list[float]:
    """Normaliza un vector a longitud unitaria (norma L2 = 1).

    Voyage devuelve vectores sin normalizar. La normalización es necesaria para
    que la similitud coseno sea equivalente al producto punto, lo que permite
    usar `vector_cosine_ops` en el índice HNSW de Postgres sin ajustes extra.
    Si la norma es 0 (vector nulo), se devuelve el vector sin modificar.
    """
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0.0:
        return vector
    return [x / norm for x in vector]


class VoyageEmbedder:
    """Cliente Voyage async con batching y normalización L2."""

    def __init__(self, api_key: str, model: str) -> None:
        # AsyncClient: usa httpx.AsyncClient internamente; no bloquea el event loop.
        self._client: voyageai.AsyncClient = voyageai.AsyncClient(api_key=api_key)
        self._model: str = model

    def batch_iterator(self, texts: list[str]) -> Generator[list[str], None, None]:
        """Divide la lista de textos en lotes de VOYAGE_BATCH_SIZE."""
        for i in range(0, len(texts), VOYAGE_BATCH_SIZE):
            yield texts[i : i + VOYAGE_BATCH_SIZE]

    async def embed_batch(self, texts: list[str]) -> EmbedBatchResult:
        """Embebe un único batch y devuelve vectores normalizados + tokens.

        Args:
            texts: Máximo VOYAGE_BATCH_SIZE textos. El caller (LLMClient.embed)
                es responsable de trocear listas más largas.

        Returns:
            EmbedBatchResult con embeddings L2-normalizados y total_tokens
            para calcular coste en llm_calls.
        """
        # input_type="document": indica a Voyage que los textos son fragmentos
        # de documentos a indexar (no queries de búsqueda). Afecta levemente a
        # la representación vectorial en algunos modelos.
        result = await self._client.embed(texts, model=self._model, input_type="document")
        normalized = [_l2_normalize(emb) for emb in result.embeddings]
        return EmbedBatchResult(
            embeddings=normalized,
            total_tokens=result.total_tokens,
        )
