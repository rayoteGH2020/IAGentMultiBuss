from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# Directorio de prompts relativo al fichero: funciona con independencia del
# directorio de trabajo desde el que se lanza la app.
PROMPTS_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=128)
def load_prompt(name: str) -> str:
    """Carga un prompt versionado desde app/llm/prompts/{name}.txt.

    Convención: nombre incluye sufijo _vN (p. ej. "extraction_v1").
    Nunca editar in-place: crear _v2 y actualizar PROMPT_VERSION en el módulo
    que lo usa. Así el historial de prompts queda en Git y es auditable.

    El cache LRU persiste el contenido del fichero en memoria durante la vida
    del proceso, eliminando I/O repetida en cada llamada LLM. maxsize=128
    cubre con holgura el número de versiones de prompt que tendrá el sistema.
    """
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        msg = f"Prompt not found: {path}"
        raise FileNotFoundError(msg)
    return path.read_text(encoding="utf-8")


def render_prompt(name: str, **kwargs: object) -> str:
    # Variante de load_prompt para prompts con placeholders {variable}.
    # Se usa cuando el prompt necesita datos dinámicos en tiempo de llamada
    # (p. ej. nombre del tenant, esquema de BD en módulo 3).
    # Los prompts de extracción simples no necesitan render_prompt.
    return load_prompt(name).format(**kwargs)
