"""Settings del chat RAG unificado (Paso 20 Fase A)."""

from __future__ import annotations

from app.config import Settings


def test_paso20_knowledge_chat_defaults() -> None:
    """Defaults de Fase A: flag activo en dev y límites de citas."""
    s = Settings(
        app_secret_key="test-secret",  # pragma: allowlist secret
        database_url="postgresql+asyncpg://x@localhost/db",  # pragma: allowlist secret
        redis_url="redis://localhost:6379/0",
    )
    assert s.knowledge_tools_enabled is True
    assert s.knowledge_chat_max_citations == 5
    # RRF scores (~0.01-0.016), no cosine 0-1; el filtro real es top_k de búsqueda.
    assert s.knowledge_chat_min_score_threshold == 0.0
