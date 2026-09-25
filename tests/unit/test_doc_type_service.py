"""Tests de validación del tipo de documento en subida."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from app.core.errors import ValidationError
from app.models import DocType, DocTypeCode
from app.services import doc_type_service
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


def test_default_doc_types_cover_enum() -> None:
    codes = {row[0] for row in doc_type_service.DEFAULT_DOC_TYPES}
    assert codes == {member.value for member in DocTypeCode}


@pytest.mark.asyncio
async def test_ensure_default_doc_types_inserts_missing() -> None:
    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()
    db.add = MagicMock()

    result = await doc_type_service.ensure_default_doc_types(db)

    expected = {member.value for member in DocTypeCode}
    assert set(result.inserted) == expected
    assert result.skipped == ()
    assert db.add.call_count == len(expected)
    assert all(isinstance(call.args[0], DocType) for call in db.add.call_args_list)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_default_doc_types_skips_existing() -> None:
    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [member.value for member in DocTypeCode]
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()
    db.add = MagicMock()

    result = await doc_type_service.ensure_default_doc_types(db)

    assert result.inserted == ()
    assert set(result.skipped) == {member.value for member in DocTypeCode}
    db.add.assert_not_called()
    db.flush.assert_not_awaited()
