from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, status

if TYPE_CHECKING:
    from uuid import UUID
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.core.logging import get_logger

log = get_logger(__name__)

_SUPERADMIN_AREA_PREFIXES = ("/sadm", "/admin/integrations")


def _is_superadmin_area_path(path: str) -> bool:
    """Rutas de consola SADM / integraciones cross-tenant."""
    return any(
        path == prefix or path.startswith(f"{prefix}/") for prefix in _SUPERADMIN_AREA_PREFIXES
    )


def _forbidden_redirect_target(request: Request) -> str:
    """Destino amigable si un usuario sin permiso SADM accede al área admin."""
    if getattr(request.state, "user", None) is not None:
        return "/"
    return "/login"


class AppError(Exception):
    """Base para excepciones de dominio de la aplicación.

    Centralizar todas las excepciones de negocio en esta jerarquía permite
    que register_error_handlers() las capture con un único handler y devuelva
    respuestas HTTP consistentes sin try/except en cada endpoint.

    status_code y code son atributos de clase para que las subclases puedan
    sobreescribirlos declarativamente sin redefinir __init__.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str, *, details: dict[str, object] | None = None):
        super().__init__(message)
        self.message = message
        # details permite adjuntar información estructurada adicional al error
        # (p. ej. qué campo falló la validación, qué recurso no se encontró).
        self.details = details or {}


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ValidationError(AppError):
    # 422 Unprocessable Content: la petición está bien formada pero los datos
    # no superan la validación de negocio (distinto de 400 Bad Request, que
    # indica un error de formato de la petición en sí).
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_error"


class AuthError(AppError):
    # 401 Unauthorized: el usuario no está autenticado o su token no es válido.
    # El handler le aplica lógica adicional de redirección (ver abajo).
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class ForbiddenError(AppError):
    # 403 Forbidden: el usuario está autenticado pero no tiene el rol necesario.
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


class ExternalServiceError(AppError):
    # 502 Bad Gateway: un servicio externo (LLM, R2, Clerk) falló o no respondió.
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "external_service_error"


class LLMCompleteError(ExternalServiceError):
    """Fallo en LLMClient.complete() tras persistir la fila en `llm_calls`."""

    def __init__(self, message: str, *, llm_call_id: UUID) -> None:
        super().__init__(
            message,
            details={"llm_call_id": str(llm_call_id)},
        )
        self.llm_call_id = llm_call_id


_INTERNAL_MESSAGE_MARKERS = (
    "ENCRYPTION_KEY",
    "APP_SECRET_KEY",
    "CLERK_SECRET_KEY",
    "POSTGRES",
    "DATABASE_URL",
    "JWKS",
    "API_KEY",
    "SECRET",
    "not configured",
    "Missing encrypted",
    "Failed to decrypt",
)


def _looks_external_forbidden(exc: AppError) -> bool:
    message = exc.message.lower()
    return "google" in message or "external" in message or "api" in message


def _looks_internal_message(message: str) -> bool:
    return any(marker.lower() in message.lower() for marker in _INTERNAL_MESSAGE_MARKERS)


def public_error_message(
    exc: AppError,
    *,
    fallback: str = "No se pudo completar la operación. Inténtalo de nuevo.",
) -> str:
    """Mensaje seguro para devolver al cliente sin filtrar detalles internos."""
    if isinstance(exc, AuthError):
        if exc.details.get("code") == "no_active_organization":
            return "No hay una organización activa."
        return "Sesión no válida. Inicia sesión de nuevo."
    if isinstance(exc, ExternalServiceError):
        return fallback
    if isinstance(exc, ForbiddenError) and _looks_external_forbidden(exc):
        return fallback
    if _looks_internal_message(exc.message):
        return fallback
    return exc.message


def public_error_details(exc: AppError) -> dict[str, object]:
    """Detalles estructurados seguros para respuestas HTTP."""
    if isinstance(exc, AuthError) and exc.details.get("code") == "no_active_organization":
        return exc.details
    if isinstance(exc, ExternalServiceError) or _looks_internal_message(exc.message):
        return {}
    if isinstance(exc, ForbiddenError) and _looks_external_forbidden(exc):
        return {}
    return exc.details


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> Response:
        # Se loguea como warning (no error): son errores de dominio esperados
        # (usuario no autenticado, recurso no encontrado), no bugs de la app.
        log.warning(
            "app_error",
            code=exc.code,
            message=exc.message,
            details=exc.details,
            path=request.url.path,
        )
        if isinstance(exc, AuthError):
            accept = request.headers.get("accept", "")
            message = public_error_message(exc)
            details = public_error_details(exc)

            if exc.details.get("code") == "no_active_organization":
                # Caso especial: el JWT es válido pero el usuario no tiene
                # organización activa en Clerk. Es un estado de onboarding,
                # no un fallo de autenticación propiamente dicho.
                if request.headers.get("HX-Request") == "true":
                    # Las peticiones HTMX no pueden seguir redirects HTTP
                    # convencionales (el swap se haría sobre el fragmento, no
                    # sobre la página completa). HX-Redirect le indica a HTMX
                    # que navegue a la URL indicada como si fuera un click.
                    return JSONResponse(
                        status_code=exc.status_code,
                        content={
                            "code": exc.code,
                            "message": message,
                            "details": details,
                        },
                        headers={"HX-Redirect": "/onboarding"},
                    )
                if "text/html" in accept and request.url.path not in {
                    "/onboarding",
                    "/auth/organization",
                    "/login",
                    "/signup",
                }:
                    # Redirect 302 para navegadores que aceptan HTML.
                    # La exclusión de rutas evita bucles de redirección
                    # (si /onboarding también lanzase AuthError, redireccionaría
                    # indefinidamente a sí mismo).
                    return RedirectResponse(url="/onboarding", status_code=302)
                # Clientes API (JSON, curl, etc.): devolver JSON puro.
                return JSONResponse(
                    status_code=exc.status_code,
                    content={
                        "code": exc.code,
                        "message": message,
                        "details": details,
                    },
                )

            # AuthError genérico (token inválido, expirado, etc.)
            if request.headers.get("HX-Request") == "true":
                # Mismo patrón HTMX: no puede seguir redirects → HX-Redirect.
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"code": exc.code, "message": message, "details": details},
                    headers={"HX-Redirect": "/login"},
                )
            if "text/html" in accept and request.url.path not in {"/login", "/signup"}:
                return RedirectResponse(url="/login", status_code=302)

        if isinstance(exc, ForbiddenError) and _is_superadmin_area_path(request.url.path):
            accept = request.headers.get("accept", "")
            redirect_url = _forbidden_redirect_target(request)
            message = public_error_message(exc)
            details = public_error_details(exc)
            if request.headers.get("HX-Request") == "true":
                return JSONResponse(
                    status_code=exc.status_code,
                    content={
                        "code": exc.code,
                        "message": message,
                        "details": details,
                    },
                    headers={"HX-Redirect": redirect_url},
                )
            if "text/html" in accept:
                return RedirectResponse(url=redirect_url, status_code=302)

        # Resto de AppErrors (NotFoundError, ValidationError, ForbiddenError…):
        # respuesta JSON estándar con el código y mensaje del error de dominio.
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": public_error_message(exc),
                "details": public_error_details(exc),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Captura cualquier excepción no controlada (bug, fallo inesperado).
        # log.exception incluye el traceback completo para diagnóstico.
        # La respuesta al cliente oculta el detalle interno: exponer trazas
        # o mensajes de error internos sería una brecha de seguridad.
        log.exception("unhandled_exception", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={"code": "internal_error", "message": "Internal server error"},
        )
