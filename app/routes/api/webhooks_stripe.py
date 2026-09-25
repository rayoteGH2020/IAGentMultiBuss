"""Webhook Stripe Billing (Paso09).

Orden: limite body → verificar firma → claim Redis por ``event.id`` → efectos.
"""

from __future__ import annotations

from typing import Any

import stripe
from fastapi import APIRouter, Header, Request

from app.config import get_settings
from app.core.db import session_scope
from app.core.errors import AuthError, ValidationError
from app.core.logging import get_logger
from app.core.webhook_ingress import (
    PROVIDER_STRIPE,
    WebhookBodyTooLarge,
    claim_webhook_event,
    read_request_body_limited,
)
from app.services import stripe_billing_service

router = APIRouter(prefix="/api/webhooks/stripe", tags=["webhooks-stripe"])
log = get_logger(__name__)


@router.post("")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(alias="Stripe-Signature"),
) -> dict[str, bool]:
    settings = get_settings()
    if not settings.stripe_webhook_secret.get_secret_value().strip():
        raise AuthError("Stripe webhook secret not configured")

    try:
        payload = await read_request_body_limited(request, settings.webhook_max_body_bytes)
    except WebhookBodyTooLarge as e:
        raise AuthError("Webhook body too large") from e

    try:
        event: dict[str, Any] = stripe_billing_service.construct_stripe_event(
            payload,
            stripe_signature,
        )
    except ValidationError as e:
        raise AuthError(str(e)) from e
    except stripe.SignatureVerificationError as e:
        log.warning("stripe.webhook.invalid_signature")
        raise AuthError("Invalid webhook signature") from e

    event_id = event.get("id")
    if not isinstance(event_id, str) or not event_id.strip():
        raise AuthError("Stripe event missing id")

    if not await claim_webhook_event(provider=PROVIDER_STRIPE, event_id=event_id):
        log.info("webhook.dedupe_replay", provider=PROVIDER_STRIPE, event_id=event_id)
        return {"received": True}

    async with session_scope() as db:
        await stripe_billing_service.handle_stripe_event(db, event)

    return {"received": True}
