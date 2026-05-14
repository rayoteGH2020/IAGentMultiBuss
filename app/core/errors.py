from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.core.logging import get_logger

log = get_logger(__name__)


class AppError(Exception):
    """Base para excepciones de dominio."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str, *, details: dict[str, object] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ValidationError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "validation_error"


class AuthError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


class ExternalServiceError(AppError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "external_service_error"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> Response:
        log.warning(
            "app_error",
            code=exc.code,
            message=exc.message,
            details=exc.details,
            path=request.url.path,
        )
        if isinstance(exc, AuthError):
            accept = request.headers.get("accept", "")
            if exc.details.get("code") == "no_active_organization":
                if request.headers.get("HX-Request") == "true":
                    return JSONResponse(
                        status_code=exc.status_code,
                        content={
                            "code": exc.code,
                            "message": exc.message,
                            "details": exc.details,
                        },
                        headers={"HX-Redirect": "/onboarding"},
                    )
                if "text/html" in accept and request.url.path not in {
                    "/onboarding",
                    "/auth/organization",
                    "/login",
                    "/signup",
                }:
                    return RedirectResponse(url="/onboarding", status_code=302)
                return JSONResponse(
                    status_code=exc.status_code,
                    content={
                        "code": exc.code,
                        "message": exc.message,
                        "details": exc.details,
                    },
                )
            if request.headers.get("HX-Request") == "true":
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"code": exc.code, "message": exc.message, "details": exc.details},
                    headers={"HX-Redirect": "/login"},
                )
            if "text/html" in accept and request.url.path not in {"/login", "/signup"}:
                return RedirectResponse(url="/login", status_code=302)
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={"code": "internal_error", "message": "Internal server error"},
        )
