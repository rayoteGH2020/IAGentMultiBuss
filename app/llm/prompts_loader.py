from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=128)
def load_prompt(name: str) -> str:
    """Carga un prompt versionado desde app/llm/prompts/{name}.txt.

    Convención: nombre incluye sufijo _vN. Nunca editar in-place: crear _v2.
    """
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        msg = f"Prompt not found: {path}"
        raise FileNotFoundError(msg)
    return path.read_text(encoding="utf-8")


def render_prompt(name: str, **kwargs: object) -> str:
    return load_prompt(name).format(**kwargs)
