"""Tests de mensajes de error de procesamiento documental."""

from app.core.document_processing_errors import (
    DocumentErrorCode,
    failure_message,
    format_user_processing_error,
    is_retryable,
    rejection_message,
)

_CIF_TECHNICAL = (
    "<failed_attempts> <generation number='1'> <exception> "
    "1 validation error for Factura cif_nif String should match pattern "
    "'^[A-Z0-9]{8,10}$' [type=string_pattern_mismatch, input_value='null', "
    "input_type=str] For further information visit https://errors.pydantic.dev/"
    "</exception> <completion> sdk_http_response=HttpResponse("
)


def test_cif_validation_error_is_user_friendly() -> None:
    message = format_user_processing_error(
        _CIF_TECHNICAL,
        filename="ejemplo_10.jpg",
    )
    assert message == (
        'Error al procesar el documento "ejemplo_10.jpg". '
        "No se ha podido validar el CIF/NIF presente en el documento."
    )


def test_missing_file_key_message() -> None:
    message = format_user_processing_error(
        "missing source_file_key",
        filename="factura.pdf",
    )
    assert "factura.pdf" in message
    assert "No encontramos el archivo subido" in message


def test_already_formatted_message_is_preserved() -> None:
    original = 'Error al procesar el documento "a.pdf". Algo pasó.'
    assert format_user_processing_error(original, filename="b.pdf") == original


def test_empty_error_uses_generic_reason() -> None:
    message = format_user_processing_error(None, filename="doc.pdf")
    assert "doc.pdf" in message
    assert "No se pudo completar la extracción" in message


def test_limit_rejections_are_not_retryable() -> None:
    assert not is_retryable(DocumentErrorCode.too_many_pages.value)
    assert not is_retryable(DocumentErrorCode.image_too_large.value)


def test_extraction_failure_and_unknown_codes_are_retryable() -> None:
    assert is_retryable(DocumentErrorCode.extraction_failed.value)
    assert is_retryable(None)
    # Un código escrito por una versión futura no debe bloquear el reintento.
    assert is_retryable("codigo_desconocido")


def test_rejection_message_includes_detail_and_admin_contact() -> None:
    message = rejection_message(
        DocumentErrorCode.too_many_pages,
        filename="anual.pdf",
        detail="12 páginas; el máximo admitido son 3",
    )
    assert "anual.pdf" in message
    assert "12 páginas" in message
    assert "administrador del sitio" in message


def test_failure_message_translates_technical_error_for_extraction_failures() -> None:
    message = failure_message(
        _CIF_TECHNICAL,
        error_code=DocumentErrorCode.extraction_failed,
        filename="ejemplo_10.jpg",
    )
    assert "CIF/NIF" in message
    assert "administrador del sitio" not in message


def test_failure_message_ignores_technical_error_for_limit_rejections() -> None:
    message = failure_message(
        "PDF with 12 pages exceeds limit of 3",
        error_code=DocumentErrorCode.too_many_pages,
        filename="anual.pdf",
        detail="12 páginas; el máximo admitido son 3",
    )
    assert "exceeds limit" not in message
    assert "12 páginas" in message
