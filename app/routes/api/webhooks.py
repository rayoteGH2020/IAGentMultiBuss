from typing import Any, cast

from fastapi import APIRouter, Header, Request
from svix.webhooks import Webhook, WebhookVerificationError

from app.config import get_settings
from app.core.db import session_scope
from app.core.errors import AuthError
from app.core.logging import get_logger
from app.services.auth_service import (
    resolve_tenant,
    resolve_user,
    revoke_clerk_membership,
    sync_clerk_membership,
)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
log = get_logger(__name__)


def _membership_event_ids(data: dict[str, Any]) -> tuple[str, str] | None:
    organization = data.get("organization")
    public_user_data = data.get("public_user_data")
    if not isinstance(organization, dict) or not isinstance(public_user_data, dict):
        return None
    clerk_org_id = organization.get("id")
    clerk_user_id = public_user_data.get("user_id")
    if not isinstance(clerk_org_id, str) or not isinstance(clerk_user_id, str):
        return None
    return clerk_org_id, clerk_user_id


@router.post("/clerk")
async def clerk_webhook(
    request: Request,
    svix_id: str = Header(alias="svix-id"),
    svix_timestamp: str = Header(alias="svix-timestamp"),
    svix_signature: str = Header(alias="svix-signature"),
) -> dict[str, bool]:
    settings = get_settings()
    secret = settings.clerk_webhook_secret.get_secret_value()
    if not secret:
        raise AuthError("Clerk webhook secret not configured")

    payload = await request.body()
    headers: dict[str, str] = {
        "svix-id": svix_id,
        "svix-timestamp": svix_timestamp,
        "svix-signature": svix_signature,
    }
    try:
        wh = Webhook(secret)
        evt = cast("dict[str, Any]", wh.verify(payload, headers))
    except WebhookVerificationError as e:
        raise AuthError("Invalid webhook signature") from e

    event_type = evt.get("type")
    data = evt.get("data", {})
    if not isinstance(data, dict):
        data = {}

    async with session_scope() as db:
        if event_type == "user.created":
            uid = data.get("id")
            if isinstance(uid, str):
                await resolve_user(db, uid)

        elif event_type == "organization.created":
            oid = data.get("id")
            if isinstance(oid, str):
                await resolve_tenant(db, oid)

        elif event_type in (
            "organizationMembership.created",
            "organizationMembership.updated",
        ):
            membership_ids = _membership_event_ids(data)
            raw_role = data.get("role", "org:member")
            role = raw_role if isinstance(raw_role, str) else "org:member"
            if membership_ids is not None:
                clerk_org_id, clerk_user_id = membership_ids
                await sync_clerk_membership(db, clerk_user_id, clerk_org_id, role)

        elif event_type == "organizationMembership.deleted":
            membership_ids = _membership_event_ids(data)
            if membership_ids is not None:
                clerk_org_id, clerk_user_id = membership_ids
                revoked = await revoke_clerk_membership(db, clerk_user_id, clerk_org_id)
                log.info(
                    "clerk.membership_deleted",
                    org_id=clerk_org_id,
                    user_id=clerk_user_id,
                    revoked=revoked,
                )

        elif event_type in ("user.deleted", "organization.deleted"):
            log.info("clerk.delete_event", type=event_type, id=data.get("id"))

    return {"received": True}
