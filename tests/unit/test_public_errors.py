from app.core.errors import (
    ExternalServiceError,
    ValidationError,
    public_error_details,
    public_error_message,
    register_error_handlers,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_public_error_message_hides_external_service_details() -> None:
    exc = ExternalServiceError(
        "Provider failed with token=secret",
        details={"body": "upstream stack trace"},
    )

    assert public_error_message(exc) == "No se pudo completar la operación. Inténtalo de nuevo."
    assert public_error_details(exc) == {}


def test_public_error_message_hides_internal_configuration_details() -> None:
    exc = ValidationError("ENCRYPTION_KEY is not configured")

    assert public_error_message(exc) == "No se pudo completar la operación. Inténtalo de nuevo."
    assert public_error_details(exc) == {}


def test_error_handler_hides_external_service_details() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise ExternalServiceError(
            "Provider failed with token=secret",
            details={"body": "secret upstream payload"},
        )

    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get("/boom")

    assert response.status_code == 502
    assert response.json() == {
        "code": "external_service_error",
        "message": "No se pudo completar la operación. Inténtalo de nuevo.",
        "details": {},
    }
    assert "secret" not in response.text
