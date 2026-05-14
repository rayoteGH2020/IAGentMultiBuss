from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.config import get_settings
from app.core.templating import render

router = APIRouter(tags=["auth"])


def _extract_host(jwks_url: str) -> str:
    return urlparse(jwks_url).netloc


@router.get("/login")
async def login_page(request: Request) -> Response:
    settings = get_settings()
    return render(
        request,
        full="pages/auth/login.html",
        ctx={
            "clerk_pub_key": settings.clerk_publishable_key,
            "clerk_frontend_host": _extract_host(settings.clerk_jwks_url),
        },
    )


@router.get("/signup")
async def signup_page(request: Request) -> HTMLResponse:
    settings = get_settings()
    return render(
        request,
        full="pages/auth/signup.html",
        ctx={
            "clerk_pub_key": settings.clerk_publishable_key,
            "clerk_frontend_host": _extract_host(settings.clerk_jwks_url),
        },
    )


@router.get("/auth/organization")
async def organization_legacy_redirect() -> RedirectResponse:
    """Alias histórico: Paso08 usa /onboarding con creación de org."""
    return RedirectResponse(url="/onboarding", status_code=302)


@router.get("/onboarding")
async def onboarding_page(request: Request) -> HTMLResponse:
    """Usuario autenticado en Clerk sin organización activa en el JWT."""
    settings = get_settings()
    return render(
        request,
        full="pages/auth/no_org.html",
        ctx={
            "clerk_pub_key": settings.clerk_publishable_key,
            "clerk_frontend_host": _extract_host(settings.clerk_jwks_url),
        },
    )


@router.get("/logout")
async def logout() -> RedirectResponse:
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("__session", path="/")
    return resp
