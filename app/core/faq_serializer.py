"""Serialización y deserialización de pares FAQ (Paso 21 B).

Formato de texto:
    P: ¿Cuál es vuestro horario?
    R: Abrimos de lunes a viernes de 9 a 18 horas.

    P: ¿Hacéis envíos?
    R: Sí, enviamos a toda España con 48h de plazo.

Este texto se almacena en KnowledgeDocument.faq_content y se sube a R2
como text/plain para que run_index_pipeline() lo procese con el pipeline estándar.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FaqPair(BaseModel):
    """Par pregunta/respuesta de un FAQ manual."""

    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=2000)


def serialize_faq(pairs: list[FaqPair]) -> str:
    """Convierte una lista de FaqPair al formato texto P:/R:."""
    return "\n\n".join(f"P: {p.question.strip()}\nR: {p.answer.strip()}" for p in pairs)


def deserialize_faq(text: str) -> list[FaqPair]:
    """Parsea el texto P:/R: de vuelta a lista de FaqPair.

    Ignora bloques malformados (sin P: o sin R:) para ser robusto ante
    ediciones manuales o contenido corrupto en faq_content.
    """
    pairs: list[FaqPair] = []
    if not text:
        return pairs
    for block in text.strip().split("\n\n"):
        question: str | None = None
        answer: str | None = None
        for line in block.strip().splitlines():
            if line.startswith("P:") and question is None:
                question = line[2:].strip()
            elif line.startswith("R:") and answer is None:
                answer = line[2:].strip()
        if question and answer:
            pairs.append(FaqPair(question=question, answer=answer))
    return pairs
