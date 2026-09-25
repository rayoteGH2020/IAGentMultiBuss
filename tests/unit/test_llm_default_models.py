"""Modelos por defecto del router LLM: vigentes y con tarifa conocida."""

import pytest
from app.llm.client import DEFAULT_MODELS
from app.llm.pricing import PRICING

# Modelos que el proveedor ya no sirve (generateContent → 404).
_RETIRED_MODELS = frozenset({"gemini-2.0-flash", "gemini-2.0-flash-lite"})


@pytest.mark.parametrize(("task", "model"), sorted(DEFAULT_MODELS.items()))
def test_default_model_is_not_retired(task: str, model: str) -> None:
    assert model not in _RETIRED_MODELS, f"{task} usa un modelo retirado: {model}"


@pytest.mark.parametrize(("task", "model"), sorted(DEFAULT_MODELS.items()))
def test_default_model_has_pricing(task: str, model: str) -> None:
    # Sin tarifa, compute_cost_eur devuelve 0 y el budget LLM del plan no cuenta.
    assert model in PRICING, f"{task}: falta {model} en app/llm/pricing.py"


def test_extraction_default_matches_validated_model() -> None:
    assert DEFAULT_MODELS["extraction"] == "gemini-3.8-flash"


def test_chat_default_matches_validated_model() -> None:
    assert DEFAULT_MODELS["chat"] == "gemini-3.5-flash-lite"
