"""Tests mínimos de prompts/schemas de contratos y seguros (Paso06)."""

from __future__ import annotations

from app.llm.prompts_loader import load_prompt
from app.schemas.contract import ContratoDocumento
from app.schemas.insurance import SeguroPoliza


def test_contract_prompt_exists_and_mentions_confidence() -> None:
    text = load_prompt("contract_extraction_v1")
    assert "confidence" in text.lower()
    assert "contrato" in text.lower()


def test_insurance_prompt_exists_and_mentions_confidence() -> None:
    text = load_prompt("insurance_extraction_v1")
    assert "confidence" in text.lower()
    assert "póliza" in text.lower() or "poliza" in text.lower() or "seguro" in text.lower()


def test_contract_and_insurance_schemas_require_confidence() -> None:
    assert "confidence" in ContratoDocumento.model_fields
    assert "confidence" in SeguroPoliza.model_fields
