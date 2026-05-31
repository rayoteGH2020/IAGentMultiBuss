from typing import Any, cast

from fastapi import APIRouter, Header, Request
from svix.webhooks import Webhook, WebhookVerificationError

from app.config import get_settings
from app.core.db import session_scope, set_tenant_context
from app.core.errors import AuthError
from app.core.logging import get_logger
from app.services.auth_service import ensure_membership, resolve_tenant, resolve_user

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
log = get_logger(__name__)


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

        elif event_type == "organizationMembership.created":
            clerk_org_id = data.get("organization", {}).get("id")
            clerk_user_id = data.get("public_user_data", {}).get("user_id")
            raw_role: str = data.get("role", "org:member")
            role = raw_role.replace("org:", "")
            if isinstance(clerk_org_id, str) and isinstance(clerk_user_id, str):
                user = await resolve_user(db, clerk_user_id)
                tenant = await resolve_tenant(db, clerk_org_id)
                await set_tenant_context(db, str(tenant.id))
                await ensure_membership(db, user.id, tenant.id, role=role)

        elif event_type == "organizationMembership.deleted":
            log.info(
                "clerk.membership_deleted",
                org_id=data.get("organization", {}).get("id"),
                user_id=data.get("public_user_data", {}).get("user_id"),
            )

        elif event_type in ("user.deleted", "organization.deleted"):
            log.info("clerk.delete_event", type=event_type, id=data.get("id"))

    return {"received": True}
