"""Tests del registro de handlers de consulta documental."""

import pytest
from app.core.errors import ValidationError
from app.models import DocTypeCode
from app.services.document_query_service import DOC_TYPE_HANDLERS, _require_handler


def test_doc_type_handlers_include_factura_and_ticket() -> None:
    assert DocTypeCode.factura.value in DOC_TYPE_HANDLERS
    assert DocTypeCode.ticket.value in DOC_TYPE_HANDLERS


def test_require_handler_unknown_code_raises_validation_error() -> None:
    with pytest.raises(ValidationError, match="not queryable"):
        _require_handler("albaran")


def test_require_handler_includes_error_details() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _require_handler("contrato")
    assert exc_info.value.details.get("error") == "document_type_not_queryable"
    assert exc_info.value.details.get("hint")
