from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from app.config import get_settings
from app.core.templating import render

router = APIRouter(tags=["auth"])

_NO_STORE_CACHE = {"Cache-Control": "no-store, max-age=0, must-revalidate"}


def _extract_host(jwks_url: str) -> str:
    return urlparse(jwks_url).netloc


def _cache_control_no_store(resp: Response) -> Response:
    for k, v in _NO_STORE_CACHE.items():
        resp.headers[k] = v
    return resp


@router.get("/login")
async def login_page(request: Request) -> Response:
    settings = get_settings()
    resp = render(
        request,
        full="pages/auth/login.html",
        ctx={
            "clerk_pub_key": settings.clerk_publishable_key,
            "clerk_frontend_host": _extract_host(settings.clerk_jwks_url),
        },
    )
    return _cache_control_no_store(resp)


@router.get("/signup")
async def signup_page(request: Request) -> Response:
    settings = get_settings()
    resp = render(
        request,
        full="pages/auth/signup.html",
        ctx={
            "clerk_pub_key": settings.clerk_publishable_key,
            "clerk_frontend_host": _extract_host(settings.clerk_jwks_url),
        },
    )
    return _cache_control_no_store(resp)


@router.get("/auth/organization")
async def organization_legacy_redirect() -> RedirectResponse:
    """Alias histórico: Paso08 usa /onboarding con creación de org."""
    return RedirectResponse(url="/onboarding", status_code=302)


@router.get("/onboarding")
async def onboarding_page(request: Request) -> Response:
    """Usuario autenticado en Clerk sin organización activa en el JWT."""
    settings = get_settings()
    resp = render(
        request,
        full="pages/auth/no_org.html",
        ctx={
            "clerk_pub_key": settings.clerk_publishable_key,
            "clerk_frontend_host": _extract_host(settings.clerk_jwks_url),
        },
    )
    return _cache_control_no_store(resp)


@router.get("/logout")
async def logout(request: Request) -> Response:
    """Cierra sesión en Clerk desde el navegador (necesario para borrar cookie __session HTTP-only)."""
    settings = get_settings()
    resp = render(
        request,
        full="pages/auth/sign_out.html",
        ctx={
            "clerk_pub_key": settings.clerk_publishable_key,
            "clerk_frontend_host": _extract_host(settings.clerk_jwks_url),
        },
    )
    return _cache_control_no_store(resp)


@router.get("/logout/done")
async def logout_done() -> RedirectResponse:
    """Limpieza server-side tras signOut del cliente por si quedaran cookies aplicables sólo desde el backend."""
    resp = RedirectResponse(url="/login", status_code=302)
    _cache_control_no_store(resp)
    resp.delete_cookie("__session", path="/")
    return resp
