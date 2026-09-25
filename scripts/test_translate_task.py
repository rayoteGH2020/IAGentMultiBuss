"""Script puntual para probar task="translate" y verlo en Langfuse.

Ejecutar con:
    infisical run -- uv run python scripts/test_translate_task.py

Requiere la app configurada (GOOGLE_API_KEY, DATABASE_URL en Infisical).
Genera una traza en Langfuse con name="llm.translate" y una fila en llm_calls.
"""

import asyncio

from app.core.db import session_scope
from app.llm.client import get_llm_client
from pydantic import BaseModel
from sqlalchemy import text


class TranslationResult(BaseModel):
    translated_text: str
    source_language: str


async def main() -> None:
    # Leer un tenant real de la BD para no violar la FK de llm_calls
    async with session_scope() as db:
        row = await db.execute(text("SELECT id FROM tenants LIMIT 1"))
        tenant_id = row.scalar_one()
    print(f"Usando tenant_id: {tenant_id}")

    async with session_scope() as db:
        client = get_llm_client()
        result = await client.complete(
            task="translate",
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Translate the following text to English. "
                        "Return translated_text and source_language.\n\n"
                        "Texto: 'Esta tarde a las 20:00 tengo que hacer tortilla de patatas.'"
                    ),
                }
            ],
            response_model=TranslationResult,
            tenant_id=tenant_id,
            db=db,
            prompt_version="test_translate_v1",
        )
        await db.commit()

    print(f"Traducción: {result.result.translated_text}")
    print(f"Idioma origen detectado: {result.result.source_language}")
    print(f"LLM call ID (buscar en Langfuse): {result.llm_call_id}")


if __name__ == "__main__":
    asyncio.run(main())
