"""Helpers for Clerk browser session cookies."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from starlette.responses import Response

    from app.config import Settings

CLERK_SESSION_COOKIE_NAME = "__session"
CLERK_SESSION_COOKIE_PATH = "/"
ClerkSessionSameSite = Literal["lax", "strict", "none"]
CLERK_SESSION_COOKIE_SAMESITE: ClerkSessionSameSite = "lax"


def clear_clerk_session_cookie(response: Response, settings: Settings) -> None:
    """Expire Clerk ``__session``; flags must match the Set-Cookie that created it."""
    secure_flags: tuple[bool, ...] = (True,) if not settings.is_dev else (True, False)
    for secure in secure_flags:
        response.delete_cookie(
            CLERK_SESSION_COOKIE_NAME,
            path=CLERK_SESSION_COOKIE_PATH,
            secure=secure,
            httponly=True,
            samesite=CLERK_SESSION_COOKIE_SAMESITE,
        )
