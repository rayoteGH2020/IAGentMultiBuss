"""Tests de mensajes de error de procesamiento documental."""

from app.core.document_processing_errors import format_user_processing_error

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
