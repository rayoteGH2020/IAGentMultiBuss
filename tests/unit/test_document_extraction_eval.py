"""Tests de los evals de extracción de tickets, contratos y pólizas."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.evals.document_compare import compare_contract, compare_insurance, compare_ticket
from app.evals.runners import document_extraction as runner
from app.schemas.contract import ContratoDocumento
from app.schemas.insurance import SeguroPoliza
from app.schemas.ticket import TicketRecibo


def _ticket(**overrides: object) -> TicketRecibo:
    data: dict[str, object] = {
        "fecha": date(2023, 9, 15),
        "comercio": "ALDI Supermercados",
        "numero_ticket": "FS002501012023007307",
        "forma_pago": "Visa Debit",
        "base_imponible": None,
        "iva_percent": None,
        "iva_amount": None,
        "total": Decimal("27.65"),
        "confidence": 0.9,
    }
    data.update(overrides)
    return TicketRecibo.model_validate(data)


def _matches(results: list[tuple[str, str, str, bool]]) -> dict[str, bool]:
    return {field: ok for field, _exp, _act, ok in results}


def test_ticket_any_of_accepts_null_and_card_variants() -> None:
    gt = {
        "fecha": "2023-09-15",
        "comercio": "ALDI",
        "numero_ticket": "FS002501012023007307",
        "forma_pago": "tarjeta",
        "base_imponible": {"any_of": ["24.85", None]},
        "iva_percent": {"any_of": [None]},
        "total": "27.65",
    }
    assert all(_matches(compare_ticket(_ticket(), gt)).values())


def test_ticket_rejects_invented_vat_percent() -> None:
    gt = {"iva_percent": {"any_of": [None]}}
    result = _matches(compare_ticket(_ticket(iva_percent=Decimal("21")), gt))
    assert result == {"iva_percent": False}


def test_ticket_code_comparison_ignores_separators() -> None:
    gt = {"numero_ticket": "SIM_282_2023/1041910"}
    result = compare_ticket(_ticket(numero_ticket="SIM 282 2023/1041910"), gt)
    assert _matches(result) == {"numero_ticket": True}


def _contract(parte: str, cif: str | None) -> ContratoDocumento:
    return ContratoDocumento(
        titulo="Contrato de arrendamiento",
        numero_contrato="GAR-NRV-037/2026",
        parte_contraria=parte,
        cif_nif=cif,
        fecha_inicio=date(2026, 10, 1),
        fecha_fin=None,
        importe=Decimal("114.95"),
        confidence=0.9,
    )


_GARAJE_GT = {
    "numero_contrato": "GAR-NRV-037/2026",
    "partes": [
        {"nombre": "Inversiones Inmobiliarias Nervión 2008", "cif_nif": "B41961533"},
        {"nombre": "Carmen Delgado Romero", "cif_nif": "28841593F"},
    ],
    "fecha_inicio": {"any_of": ["2026-09-23", "2026-10-01"]},
    "fecha_fin": {"any_of": ["2027-09-30", None]},
    "importe": {"any_of": ["95", "114.95"]},
}


@pytest.mark.parametrize(
    ("parte", "cif"),
    [
        ("Dª. Carmen Delgado Romero", "28841593F"),
        ("Inversiones Inmobiliarias Nervión 2008, S.L.", "B41961533"),
    ],
)
def test_contract_accepts_either_party_with_its_own_id(parte: str, cif: str) -> None:
    assert all(_matches(compare_contract(_contract(parte, cif), _GARAJE_GT)).values())


def test_contract_rejects_id_of_the_other_party() -> None:
    result = _matches(compare_contract(_contract("Carmen Delgado Romero", "B41961533"), _GARAJE_GT))
    assert result["parte_contraria"] is True
    assert result["cif_nif"] is False


def test_insurance_premium_accepts_total_or_net() -> None:
    poliza = SeguroPoliza(
        aseguradora="Iberia Mutual Seguros y Reaseguros, S.A.",
        numero_poliza="DC-30-2026-017724",
        tomador="José María Hernández Martínez",
        cif_nif="22981437K",
        fecha_inicio=date(2026, 10, 1),
        fecha_fin=date(2027, 10, 1),
        prima=Decimal("381.40"),
        confidence=0.9,
    )
    gt = {
        "aseguradora": "Iberia Mutual Seguros",
        "numero_poliza": "DC-30-2026-017724",
        "tomador": "José María Hernández Martínez",
        "cif_nif": "22981437K",
        "fecha_inicio": "2026-10-01",
        "fecha_fin": "2027-10-01",
        "prima": {"any_of": ["412.48", "381.40"]},
    }
    assert all(_matches(compare_insurance(poliza, gt)).values())


@pytest.mark.parametrize("doc_type", sorted(runner.SPECS))
def test_dataset_fixtures_exist_and_have_ground_truth(doc_type: str) -> None:
    dataset = runner.load_dataset(doc_type)
    assert dataset["doc_type"] == doc_type
    ids = [case["id"] for case in dataset["cases"]]
    assert len(ids) == len(set(ids))
    for case in dataset["cases"]:
        assert (runner.FIXTURES_ROOT / case["file"]).is_file(), case["file"]
        assert case["ground_truth"], case["id"]


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    @asynccontextmanager
    async def _factory(_tenant_id: uuid.UUID) -> AsyncIterator[MagicMock]:
        yield db

    monkeypatch.setattr(runner, "session_factory_for_worker", _factory)
    return db


@pytest.mark.asyncio
async def test_run_case_scores_extracted_ticket(fake_session: MagicMock) -> None:
    extract = AsyncMock(return_value=SimpleNamespace(ticket=_ticket()))
    spec = runner.DocEvalSpec("tickets_v1.json", extract, "ticket", compare_ticket)
    case = {
        "id": "tk_aldi",
        "file": "invoices/ejemplo_03.jpg",
        "ground_truth": {"total": "27.65", "comercio": "ALDI"},
    }

    result = await runner.run_case(case, spec, uuid.uuid4())

    assert result.success is True
    assert result.field_accuracy == 1.0
    assert extract.await_args is not None
    assert extract.await_args.kwargs["mime_type"] == "image/jpeg"
    fake_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_case_reports_extraction_error(fake_session: MagicMock) -> None:
    extract = AsyncMock(side_effect=RuntimeError("boom"))
    spec = runner.DocEvalSpec("tickets_v1.json", extract, "ticket", compare_ticket)
    case = {"id": "tk", "file": "invoices/ejemplo_03.jpg", "ground_truth": {"total": "1"}}

    result = await runner.run_case(case, spec, uuid.uuid4())

    assert result.success is False
    assert result.error == "RuntimeError: boom"
    fake_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_case_missing_fixture_is_failure() -> None:
    spec = runner.DocEvalSpec("tickets_v1.json", AsyncMock(), "ticket", compare_ticket)
    case = {"id": "tk", "file": "invoices/no_existe.png", "ground_truth": {"total": "1"}}

    result = await runner.run_case(case, spec, uuid.uuid4())

    assert result.success is False
    assert result.error == "fixture not found: invoices/no_existe.png"


def test_selected_types_rejects_unknown() -> None:
    assert runner._selected_types([]) == list(runner.SPECS)
    assert runner._selected_types(["seguro"]) == ["seguro"]
    with pytest.raises(SystemExit):
        runner._selected_types(["poliza"])
