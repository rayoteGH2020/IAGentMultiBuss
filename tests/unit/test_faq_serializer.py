"""Tests unitarios del serializador/deserializador de FAQ (Paso 21 B.6)."""

from __future__ import annotations

from app.core.faq_serializer import FaqPair, deserialize_faq, serialize_faq

# ---------------------------------------------------------------------------
# serialize_faq
# ---------------------------------------------------------------------------


def test_serialize_single_pair() -> None:
    pairs = [FaqPair(question="¿Cuál es vuestro horario?", answer="Abrimos de lunes a viernes.")]
    result = serialize_faq(pairs)
    assert result == "P: ¿Cuál es vuestro horario?\nR: Abrimos de lunes a viernes."


def test_serialize_multiple_pairs() -> None:
    pairs = [
        FaqPair(question="¿Cuál es vuestro horario?", answer="De 9 a 18."),
        FaqPair(question="¿Hacéis envíos?", answer="Sí, a toda España."),
    ]
    result = serialize_faq(pairs)
    blocks = result.split("\n\n")
    assert len(blocks) == 2
    assert blocks[0].startswith("P: ¿Cuál es vuestro horario?")
    assert blocks[1].startswith("P: ¿Hacéis envíos?")


def test_serialize_strips_whitespace() -> None:
    pairs = [FaqPair(question="  Pregunta  ", answer="  Respuesta  ")]
    result = serialize_faq(pairs)
    assert "P: Pregunta\nR: Respuesta" in result


def test_serialize_empty_list() -> None:
    assert serialize_faq([]) == ""


# ---------------------------------------------------------------------------
# deserialize_faq
# ---------------------------------------------------------------------------


def test_deserialize_single_pair() -> None:
    text = "P: ¿Cuál es vuestro horario?\nR: De 9 a 18."
    pairs = deserialize_faq(text)
    assert len(pairs) == 1
    assert pairs[0].question == "¿Cuál es vuestro horario?"
    assert pairs[0].answer == "De 9 a 18."


def test_deserialize_multiple_pairs() -> None:
    text = "P: ¿Horario?\nR: De 9 a 18.\n\nP: ¿Envíos?\nR: Sí, a toda España."
    pairs = deserialize_faq(text)
    assert len(pairs) == 2
    assert pairs[0].question == "¿Horario?"
    assert pairs[1].question == "¿Envíos?"


def test_deserialize_empty_string() -> None:
    assert deserialize_faq("") == []


def test_deserialize_ignores_malformed_blocks() -> None:
    """Bloques sin P: o sin R: se ignoran silenciosamente."""
    text = "Solo texto sin formato\n\nP: Pregunta válida\nR: Respuesta válida"
    pairs = deserialize_faq(text)
    assert len(pairs) == 1
    assert pairs[0].question == "Pregunta válida"


# ---------------------------------------------------------------------------
# round-trip: serialize → deserialize
# ---------------------------------------------------------------------------


def test_roundtrip() -> None:
    original = [
        FaqPair(question="¿Cuál es vuestro horario?", answer="Lunes a viernes de 9 a 18."),
        FaqPair(
            question="¿Aceptáis devoluciones?", answer="Sí, en los 30 días siguientes a la compra."
        ),
        FaqPair(question="¿Cómo contactar?", answer="Por email a hola@empresa.com."),
    ]
    serialized = serialize_faq(original)
    recovered = deserialize_faq(serialized)
    assert len(recovered) == len(original)
    for orig, rec in zip(original, recovered, strict=True):
        assert rec.question == orig.question.strip()
        assert rec.answer == orig.answer.strip()
