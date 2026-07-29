"""Clerk Backend API client (Paso 50).

Punto único de acceso a operaciones administrativas de Clerk.
Solo se usa desde app/services/admin_service.py — no importar directamente desde routes.
"""

from __future__ import annotations

from typing import Any, cast

import httpx

from app.config import get_settings
from app.core.errors import ExternalServiceError

_BASE = "https://api.clerk.com/v1"


def _headers() -> dict[str, str]:
    secret = get_settings().clerk_secret_key.get_secret_value()
    return {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}


async def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.request(method, f"{_BASE}{path}", headers=_headers(), **kwargs)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ExternalServiceError(
                f"Clerk API {exc.response.status_code}: {exc.response.text}"
            ) from exc
        if r.status_code == 204:
            return {}
        return cast("dict[str, Any]", r.json())


# --- Organizations ---


async def create_organization(name: str) -> dict[str, Any]:
    return await _request("POST", "/organizations", json={"name": name})


async def delete_organization(clerk_org_id: str) -> None:
    await _request("DELETE", f"/organizations/{clerk_org_id}")


async def list_organizations(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    return await _request("GET", "/organizations", params={"limit": limit, "offset": offset})


# --- Users ---


async def create_user(
    email: str,
    password: str,
    first_name: str = "",
    last_name: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {"email_address": [email], "password": password}
    if first_name:
        payload["first_name"] = first_name
    if last_name:
        payload["last_name"] = last_name
    return await _request("POST", "/users", json=payload)


async def delete_user(clerk_user_id: str) -> None:
    await _request("DELETE", f"/users/{clerk_user_id}")


# --- Organization memberships ---


async def add_org_member(
    clerk_org_id: str, clerk_user_id: str, role: str = "org:member"
) -> dict[str, Any]:
    return await _request(
        "POST",
        f"/organizations/{clerk_org_id}/memberships",
        json={"user_id": clerk_user_id, "role": role},
    )


async def remove_org_member(clerk_org_id: str, clerk_user_id: str) -> None:
    await _request("DELETE", f"/organizations/{clerk_org_id}/memberships/{clerk_user_id}")


async def update_org_member_role(
    clerk_org_id: str,
    clerk_user_id: str,
    role: str,
) -> dict[str, Any]:
    return await _request(
        "PATCH",
        f"/organizations/{clerk_org_id}/memberships/{clerk_user_id}",
        json={"role": role},
    )


async def create_org_invitation(
    clerk_org_id: str,
    email: str,
    role: str = "org:member",
) -> dict[str, Any]:
    return await _request(
        "POST",
        f"/organizations/{clerk_org_id}/invitations",
        json={"email_address": email, "role": role},
    )


async def update_user(
    clerk_user_id: str,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if first_name is not None:
        payload["first_name"] = first_name
    if last_name is not None:
        payload["last_name"] = last_name
    if not payload:
        return await _request("GET", f"/users/{clerk_user_id}")
    return await _request("PATCH", f"/users/{clerk_user_id}", json=payload)


async def find_user_by_email(email: str) -> dict[str, Any] | None:
    result = await _request("GET", "/users", params={"email_address": [email], "limit": 1})
    data = result.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            return first
    return None
