"""Tests de tipificación por fichero en subida de documentos."""

from __future__ import annotations

import pytest
from app.core.errors import ValidationError
from app.models import DocTypeCode
from app.services import doc_type_service


def test_normalize_form_str_list_accepts_str_and_list() -> None:
    assert doc_type_service.normalize_form_str_list(None) == []
    assert doc_type_service.normalize_form_str_list("factura") == ["factura"]
    assert doc_type_service.normalize_form_str_list(["factura", "contrato"]) == [
        "factura",
        "contrato",
    ]


def test_resolve_per_file_doc_types_pairs_one_to_one() -> None:
    types = doc_type_service.resolve_per_file_doc_types(
        file_count=2,
        doc_type_codes=["factura", "contrato"],
    )
    assert types == [DocTypeCode.factura, DocTypeCode.contrato]


def test_resolve_per_file_doc_types_accepts_single_str() -> None:
    types = doc_type_service.resolve_per_file_doc_types(
        file_count=1,
        doc_type_codes="ticket",
    )
    assert types == [DocTypeCode.ticket]


def test_resolve_per_file_doc_types_rejects_count_mismatch() -> None:
    with pytest.raises(ValidationError):
        doc_type_service.resolve_per_file_doc_types(
            file_count=2,
            doc_type_codes=["factura"],
        )


def test_resolve_per_file_doc_types_rejects_missing() -> None:
    with pytest.raises(ValidationError):
        doc_type_service.resolve_per_file_doc_types(file_count=1, doc_type_codes=None)
