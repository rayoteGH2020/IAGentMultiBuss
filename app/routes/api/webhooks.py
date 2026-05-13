from typing import Any, cast

from fastapi import APIRouter, Header, Request
from svix.webhooks import Webhook, WebhookVerificationError

from app.config import get_settings
from app.core.db import session_scope
from app.core.errors import AuthError
from app.core.logging import get_logger
from app.services.auth_service import resolve_tenant, resolve_user

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
        elif event_type in ("user.deleted", "organization.deleted"):
            log.info("clerk.delete_event", type=event_type, id=data.get("id"))

    return {"received": True}
