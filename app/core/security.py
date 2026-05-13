from typing import Any, cast

import httpx
import jwt
from jwt import PyJWKClient

from app.config import get_settings
from app.core.errors import AuthError

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        settings = get_settings()
        if not settings.clerk_jwks_url:
            raise AuthError("Clerk JWKS URL not configured")
        _jwks_client = PyJWKClient(settings.clerk_jwks_url, cache_keys=True)
    return _jwks_client


def verify_clerk_jwt(token: str) -> dict[str, Any]:
    """Valida un JWT de Clerk y devuelve los claims."""
    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        decoded: object = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        if not isinstance(decoded, dict):
            raise AuthError("Invalid token structure")
        return cast("dict[str, Any]", decoded)
    except jwt.ExpiredSignatureError as e:
        raise AuthError("Token expired") from e
    except jwt.InvalidTokenError as e:
        raise AuthError(f"Invalid token: {e}") from e


async def fetch_clerk_user(clerk_user_id: str) -> dict[str, Any]:
    """Obtiene el perfil completo de un usuario desde la API de Clerk."""
    settings = get_settings()
    secret = settings.clerk_secret_key.get_secret_value()
    if not secret:
        raise AuthError("Clerk secret key not configured")
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://api.clerk.com/v1/users/{clerk_user_id}",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=10.0,
        )
        r.raise_for_status()
        return cast("dict[str, Any]", r.json())


async def fetch_clerk_org(clerk_org_id: str) -> dict[str, Any]:
    settings = get_settings()
    secret = settings.clerk_secret_key.get_secret_value()
    if not secret:
        raise AuthError("Clerk secret key not configured")
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://api.clerk.com/v1/organizations/{clerk_org_id}",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=10.0,
        )
        r.raise_for_status()
        return cast("dict[str, Any]", r.json())
