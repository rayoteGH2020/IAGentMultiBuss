"""Tests mínimos de prompts/schemas de contratos y seguros (Paso06)."""

from __future__ import annotations

from app.llm.extraction import CONTRACT_PROMPT_VERSION
from app.llm.prompts_loader import load_prompt
from app.schemas.contract import ContratoDocumento
from app.schemas.insurance import SeguroPoliza


def test_contract_prompt_exists_and_mentions_confidence() -> None:
    assert CONTRACT_PROMPT_VERSION == "contract_extraction_v2"
    text = load_prompt(CONTRACT_PROMPT_VERSION)
    assert "confidence" in text.lower()
    assert "contrato" in text.lower()
    for field in ("fecha_firma", "importe_periodico", "periodicidad", "importe_total"):
        assert field in text


def test_contract_prompt_examples_do_not_leak_eval_answers() -> None:
    """Los ejemplos del prompt no deben ser cifras de contracts_v2 (falsearían la eval)."""
    text = load_prompt(CONTRACT_PROMPT_VERSION)
    for eval_value in ("13.800", "19.200", "9.800", "118,50", "95,00", "21 de septiembre"):
        assert eval_value not in text


def test_insurance_prompt_exists_and_mentions_confidence() -> None:
    text = load_prompt("insurance_extraction_v1")
    assert "confidence" in text.lower()
    assert "póliza" in text.lower() or "poliza" in text.lower() or "seguro" in text.lower()


def test_contract_and_insurance_schemas_require_confidence() -> None:
    assert "confidence" in ContratoDocumento.model_fields
    assert "confidence" in SeguroPoliza.model_fields
