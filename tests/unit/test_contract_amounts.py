"""Contratos v2 (P2b-3): cuota + periodicidad, total e importe anual equivalente."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.models import Contract, ContractStatus
from app.schemas.contract import PAYMENTS_PER_YEAR, PERIOD_SUFFIX, ContratoDocumento, annual_amount
from app.services import document_panel_service
from pydantic import ValidationError


@pytest.mark.parametrize(
    ("cuota", "periodicidad", "anual"),
    [
        ("95", "mensual", "1140.00"),
        ("711", "trimestral", "2844.00"),
        ("600", "semestral", "1200.00"),
        ("1874.21", "anual", "1874.21"),
    ],
)
def test_annual_amount_per_period(cuota: str, periodicidad: str, anual: str) -> None:
    assert annual_amount(Decimal(cuota), periodicidad) == Decimal(anual)


@pytest.mark.parametrize(
    ("cuota", "periodicidad"),
    [(None, "mensual"), (Decimal("9800"), "unico"), (Decimal("95"), None)],
)
def test_annual_amount_is_none_without_recurring_fee(
    cuota: Decimal | None, periodicidad: str | None
) -> None:
    assert annual_amount(cuota, periodicidad) is None


def test_every_recurring_period_has_a_ui_suffix() -> None:
    assert set(PERIOD_SUFFIX) == set(PAYMENTS_PER_YEAR)


def _contrato(**kwargs: object) -> ContratoDocumento:
    base: dict[str, object] = {
        "titulo": "Arrendamiento",
        "parte_contraria": "Inmobiliaria SL",
        "fecha_inicio": "2026-10-01",
        "confidence": 0.9,
    }
    base.update(kwargs)
    return ContratoDocumento.model_validate(base)


def test_schema_computes_annual_amount_but_does_not_ask_the_model_for_it() -> None:
    contrato = _contrato(importe_periodico=95, periodicidad="mensual", fecha_firma="2026-09-23")

    assert contrato.importe_anual == Decimal("1140.00")
    assert contrato.fecha_firma == date(2026, 9, 23)
    assert "importe_anual" not in ContratoDocumento.model_json_schema()["properties"]


def test_schema_rejects_unknown_period() -> None:
    with pytest.raises(ValidationError):
        _contrato(importe_periodico=95, periodicidad="bimensual")


def _contract(**kwargs: object) -> Contract:
    now = datetime.now(tz=UTC)
    return Contract(
        id=uuid4(),
        tenant_id=uuid4(),
        doc_type_id=uuid4(),
        status=ContractStatus.ready,
        parte_contraria="Vendedor",
        fecha_inicio=date(2026, 9, 18),
        created_at=now,
        updated_at=now,
        **kwargs,
    )


def test_panel_shows_total_without_suffix_for_one_off_payment() -> None:
    row = document_panel_service.row_from_contract(
        _contract(importe_total=Decimal("9800"), periodicidad="unico")
    )

    assert row.total == Decimal("9800")
    assert row.total_suffix is None


def test_panel_shows_fee_with_its_period() -> None:
    row = document_panel_service.row_from_contract(
        _contract(
            importe_periodico=Decimal("711"),
            periodicidad="trimestral",
            importe_total=Decimal("2844"),
        )
    )

    assert row.total == Decimal("711")
    assert row.total_suffix == "/ trimestre"
