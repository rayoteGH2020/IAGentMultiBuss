"""Tests de validación del tipo de documento en subida."""

import pytest
from app.core.errors import ValidationError
from app.models import DocTypeCode
from app.services.doc_type_service import parse_doc_type_form_value, require_doc_type_form_value


def test_require_doc_type_form_value_accepts_factura() -> None:
    assert require_doc_type_form_value("factura") == DocTypeCode.factura


def test_require_doc_type_form_value_rejects_empty() -> None:
    with pytest.raises(ValidationError, match="required"):
        require_doc_type_form_value("")


def test_require_doc_type_form_value_rejects_none() -> None:
    with pytest.raises(ValidationError, match="required"):
        require_doc_type_form_value(None)


def test_parse_doc_type_form_value_still_allows_empty() -> None:
    assert parse_doc_type_form_value("") is None
