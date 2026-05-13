from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import get_settings
from app.core.templating import render

router = APIRouter(tags=["auth"])


def _extract_host(jwks_url: str) -> str:
    return urlparse(jwks_url).netloc


@router.get("/login")
async def login_page(request: Request) -> HTMLResponse:
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


@router.get("/logout")
async def logout() -> RedirectResponse:
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("__session", path="/")
    return resp
