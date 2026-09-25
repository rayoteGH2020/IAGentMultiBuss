"""Billing Stripe: Checkout, Customer Portal y webhooks (Paso09).

Stripe es fuente de pago; los permisos internos siguen en ``plan_code`` via
``plan_service.assign_tenant_plan``. Decisiones comerciales:

- ``past_due`` / ``invoice.payment_failed``: marca ``billing_status`` y mantiene el plan.
- suscripcion cancelada/eliminada: downgrade a ``basic`` y ``billing_status=canceled``.
- suscripcion activa: asigna el plan mapeado por ``plans.stripe_price_id``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import stripe
import structlog
from sqlalchemy import select

from app.config import Settings, get_settings
from app.core.db import set_tenant_context
from app.core.entitlement_codes import PLAN_CODES, normalize_plan_code
from app.core.errors import NotFoundError, ValidationError
from app.models.plan import Plan
from app.models.tenant import Tenant
from app.services import audit_service, plan_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

BILLING_STATUS_NONE = "none"
BILLING_STATUS_ACTIVE = "active"
BILLING_STATUS_PAST_DUE = "past_due"
BILLING_STATUS_CANCELED = "canceled"

ACTION_BILLING_STATUS = "billing.status_changed"
ACTION_BILLING_CUSTOMER_LINKED = "billing.stripe_customer_linked"
RESOURCE_TENANT = "tenant"

DEFAULT_DOWNGRADE_PLAN = "basic"


def is_stripe_configured(settings: Settings | None = None) -> bool:
    cfg = settings or get_settings()
    return bool(cfg.stripe_secret_key.get_secret_value().strip())


def _configure_stripe(settings: Settings | None = None) -> Settings:
    cfg = settings or get_settings()
    secret = cfg.stripe_secret_key.get_secret_value().strip()
    if not secret:
        raise ValidationError("Stripe no esta configurado (STRIPE_SECRET_KEY).")
    stripe.api_key = secret
    return cfg


def construct_stripe_event(payload: bytes, signature_header: str) -> dict[str, Any]:
    """Verifica firma Stripe; lanza ``stripe.SignatureVerificationError`` si falla."""
    cfg = get_settings()
    secret = cfg.stripe_webhook_secret.get_secret_value().strip()
    if not secret:
        raise ValidationError("Stripe webhook secret no configurado.")
    event = stripe.Webhook.construct_event(payload, signature_header, secret)
    return dict(event)


async def list_purchasable_plans(db: AsyncSession) -> list[Plan]:
    result = await db.execute(
        select(Plan)
        .where(
            Plan.is_active.is_(True),
            Plan.is_public.is_(True),
            Plan.stripe_price_id.is_not(None),
            Plan.stripe_price_id != "",
        )
        .order_by(Plan.sort_order.asc(), Plan.code.asc())
    )
    return list(result.scalars().all())


async def _require_tenant(db: AsyncSession, tenant_id: UUID) -> Tenant:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise NotFoundError(f"Tenant {tenant_id} not found")
    return tenant


async def _tenant_by_stripe_customer(
    db: AsyncSession,
    customer_id: str | None,
) -> Tenant | None:
    if not customer_id:
        return None
    result = await db.execute(select(Tenant).where(Tenant.stripe_customer_id == customer_id))
    return result.scalar_one_or_none()


async def _set_billing_fields(
    db: AsyncSession,
    tenant: Tenant,
    *,
    billing_status: str | None = None,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    clear_subscription: bool = False,
    actor_user_id: UUID | None = None,
    reason: str | None = None,
) -> None:
    previous = tenant.billing_status
    if stripe_customer_id and tenant.stripe_customer_id != stripe_customer_id:
        tenant.stripe_customer_id = stripe_customer_id
    if clear_subscription:
        tenant.stripe_subscription_id = None
    elif stripe_subscription_id:
        tenant.stripe_subscription_id = stripe_subscription_id
    if billing_status is not None and tenant.billing_status != billing_status:
        tenant.billing_status = billing_status
        await set_tenant_context(db, str(tenant.id))
        await audit_service.log_action(
            db,
            tenant_id=tenant.id,
            user_id=actor_user_id,
            action=ACTION_BILLING_STATUS,
            resource_type=RESOURCE_TENANT,
            resource_id=tenant.id,
            metadata={
                "from_status": previous,
                "to_status": billing_status,
                "reason": reason,
                "source": plan_service.SOURCE_STRIPE,
            },
        )
    await db.flush()


async def ensure_stripe_customer(
    db: AsyncSession,
    *,
    tenant: Tenant,
    actor_email: str | None = None,
) -> str:
    """Crea o reutiliza el Customer de Stripe del tenant."""
    cfg = _configure_stripe()
    if tenant.stripe_customer_id:
        return tenant.stripe_customer_id

    customer_kwargs: dict[str, Any] = {
        "name": tenant.name,
        "metadata": {
            "tenant_id": str(tenant.id),
            "app_env": cfg.app_env,
        },
    }
    if actor_email:
        customer_kwargs["email"] = actor_email
    customer = stripe.Customer.create(**customer_kwargs)
    customer_id = str(customer["id"])
    tenant.stripe_customer_id = customer_id
    await db.flush()
    await set_tenant_context(db, str(tenant.id))
    await audit_service.log_action(
        db,
        tenant_id=tenant.id,
        user_id=None,
        action=ACTION_BILLING_CUSTOMER_LINKED,
        resource_type=RESOURCE_TENANT,
        resource_id=tenant.id,
        metadata={"stripe_customer_id": customer_id, "source": plan_service.SOURCE_STRIPE},
    )
    return customer_id


async def create_checkout_session(
    db: AsyncSession,
    *,
    tenant: Tenant,
    plan_code: str,
    actor_user_id: UUID,
    actor_email: str | None = None,
) -> str:
    """Devuelve la URL de Stripe Checkout para suscribirse al plan."""
    cfg = _configure_stripe()
    code = normalize_plan_code(plan_code)
    if code not in PLAN_CODES:
        raise ValidationError(f"Plan code '{plan_code}' is not valid")
    plan = await plan_service.require_plan_by_code(db, code)
    if not plan.stripe_price_id:
        raise ValidationError(f"El plan '{code}' no tiene price_id de Stripe.")
    if not plan.is_active or not plan.is_public:
        raise ValidationError(f"El plan '{code}' no esta disponible para compra.")

    customer_id = await ensure_stripe_customer(
        db,
        tenant=tenant,
        actor_email=actor_email,
    )
    success_url = f"{cfg.app_base_url.rstrip('/')}/settings/billing?checkout=success"
    cancel_url = f"{cfg.app_base_url.rstrip('/')}/settings/billing?checkout=cancel"
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        client_reference_id=str(tenant.id),
        line_items=[{"price": plan.stripe_price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            "tenant_id": str(tenant.id),
            "plan_code": plan.code,
            "actor_user_id": str(actor_user_id),
        },
        subscription_data={
            "metadata": {
                "tenant_id": str(tenant.id),
                "plan_code": plan.code,
            }
        },
        allow_promotion_codes=True,
    )
    url = session["url"]
    if not isinstance(url, str) or not url:
        raise ValidationError("Stripe no devolvio URL de Checkout.")
    logger.info(
        "stripe.checkout.created",
        tenant_id=str(tenant.id),
        plan_code=plan.code,
        session_id=session["id"],
    )
    return url


async def create_billing_portal_session(
    db: AsyncSession,
    *,
    tenant: Tenant,
) -> str:
    cfg = _configure_stripe()
    if not tenant.stripe_customer_id:
        raise ValidationError("Este tenant aun no tiene cliente Stripe.")
    portal = stripe.billing_portal.Session.create(
        customer=tenant.stripe_customer_id,
        return_url=f"{cfg.app_base_url.rstrip('/')}/settings/billing",
    )
    url = portal["url"]
    if not isinstance(url, str) or not url:
        raise ValidationError("Stripe no devolvio URL del portal.")
    return url


def _price_id_from_subscription(subscription: dict[str, Any]) -> str | None:
    items = subscription.get("items") or {}
    data = items.get("data") if isinstance(items, dict) else None
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    if not isinstance(first, dict):
        return None
    price = first.get("price")
    if isinstance(price, str) and price:
        return price
    if isinstance(price, dict):
        pid = price.get("id")
        return pid if isinstance(pid, str) else None
    return None


def _tenant_id_from_metadata(metadata: object) -> UUID | None:
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get("tenant_id")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return UUID(raw.strip())
    except ValueError:
        return None


async def _assign_plan_from_price(
    db: AsyncSession,
    *,
    tenant: Tenant,
    price_id: str | None,
    reason: str,
    event_id: str | None = None,
) -> None:
    if not price_id:
        logger.warning(
            "stripe.price_missing",
            tenant_id=str(tenant.id),
            reason=reason,
        )
        return
    plan = await plan_service.get_plan_by_stripe_price_id(db, price_id)
    if plan is None:
        logger.error(
            "stripe.price_unmapped",
            tenant_id=str(tenant.id),
            stripe_price_id=price_id,
            reason=reason,
        )
        return
    await plan_service.assign_tenant_plan(
        db,
        tenant_id=tenant.id,
        plan_code=plan.code,
        actor_user_id=None,
        reason=reason,
        source=plan_service.SOURCE_STRIPE,
        metadata={"stripe_price_id": price_id, "stripe_event_id": event_id},
    )


async def handle_stripe_event(db: AsyncSession, event: dict[str, Any]) -> None:
    """Aplica efectos de un evento Stripe ya verificado y reclamado."""
    event_type = event.get("type")
    event_id = event.get("id") if isinstance(event.get("id"), str) else None
    data_wrapper = event.get("data")
    data_object = data_wrapper.get("object") if isinstance(data_wrapper, dict) else None
    if not isinstance(data_object, dict):
        logger.warning("stripe.event.missing_object", type=event_type, event_id=event_id)
        return

    if event_type == "checkout.session.completed":
        await _on_checkout_completed(db, data_object, event_id=event_id)
    elif event_type in (
        "customer.subscription.created",
        "customer.subscription.updated",
    ):
        await _on_subscription_upsert(db, data_object, event_id=event_id)
    elif event_type == "customer.subscription.deleted":
        await _on_subscription_deleted(db, data_object, event_id=event_id)
    elif event_type == "invoice.payment_failed":
        await _on_payment_failed(db, data_object, event_id=event_id)
    else:
        logger.info("stripe.event.ignored", type=event_type, event_id=event_id)


async def _on_checkout_completed(
    db: AsyncSession,
    session: dict[str, Any],
    *,
    event_id: str | None,
) -> None:
    if session.get("mode") != "subscription":
        return
    tenant_id = _tenant_id_from_metadata(session.get("metadata"))
    if tenant_id is None:
        ref = session.get("client_reference_id")
        if isinstance(ref, str) and ref.strip():
            try:
                tenant_id = UUID(ref.strip())
            except ValueError:
                tenant_id = None
    if tenant_id is None:
        logger.error("stripe.checkout.missing_tenant", event_id=event_id)
        return

    tenant = await _require_tenant(db, tenant_id)
    customer_id = session.get("customer")
    subscription_id = session.get("subscription")
    raw_meta = session.get("metadata")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    plan_code_raw = meta.get("plan_code")
    plan_code = plan_code_raw if isinstance(plan_code_raw, str) else None

    await _set_billing_fields(
        db,
        tenant,
        billing_status=BILLING_STATUS_ACTIVE,
        stripe_customer_id=str(customer_id) if customer_id else None,
        stripe_subscription_id=str(subscription_id) if subscription_id else None,
        reason="checkout.session.completed",
    )
    if plan_code:
        await plan_service.assign_tenant_plan(
            db,
            tenant_id=tenant.id,
            plan_code=plan_code,
            actor_user_id=None,
            reason="checkout.session.completed",
            source=plan_service.SOURCE_STRIPE,
            metadata={"stripe_event_id": event_id},
        )


async def _on_subscription_upsert(
    db: AsyncSession,
    subscription: dict[str, Any],
    *,
    event_id: str | None,
) -> None:
    customer_id = subscription.get("customer")
    customer_str = str(customer_id) if customer_id else None
    tenant = await _tenant_by_stripe_customer(db, customer_str)
    if tenant is None:
        tenant_id = _tenant_id_from_metadata(subscription.get("metadata"))
        if tenant_id is not None:
            tenant = await _require_tenant(db, tenant_id)
    if tenant is None:
        logger.error(
            "stripe.subscription.tenant_not_found",
            customer_id=customer_str,
            event_id=event_id,
        )
        return

    status = str(subscription.get("status") or "")
    sub_id = subscription.get("id")
    price_id = _price_id_from_subscription(subscription)

    if status in {"active", "trialing"}:
        await _set_billing_fields(
            db,
            tenant,
            billing_status=BILLING_STATUS_ACTIVE,
            stripe_customer_id=customer_str,
            stripe_subscription_id=str(sub_id) if sub_id else None,
            reason=f"subscription.{status}",
        )
        await _assign_plan_from_price(
            db,
            tenant=tenant,
            price_id=price_id,
            reason=f"customer.subscription:{status}",
            event_id=event_id,
        )
    elif status == "past_due":
        await _set_billing_fields(
            db,
            tenant,
            billing_status=BILLING_STATUS_PAST_DUE,
            stripe_customer_id=customer_str,
            stripe_subscription_id=str(sub_id) if sub_id else None,
            reason="subscription.past_due",
        )
    elif status in {"canceled", "unpaid", "incomplete_expired"}:
        await _set_billing_fields(
            db,
            tenant,
            billing_status=BILLING_STATUS_CANCELED,
            stripe_customer_id=customer_str,
            clear_subscription=True,
            reason=f"subscription.{status}",
        )
        await plan_service.assign_tenant_plan(
            db,
            tenant_id=tenant.id,
            plan_code=DEFAULT_DOWNGRADE_PLAN,
            actor_user_id=None,
            reason=f"customer.subscription:{status}",
            source=plan_service.SOURCE_STRIPE,
            metadata={"stripe_event_id": event_id},
        )


async def _on_subscription_deleted(
    db: AsyncSession,
    subscription: dict[str, Any],
    *,
    event_id: str | None,
) -> None:
    customer_id = subscription.get("customer")
    tenant = await _tenant_by_stripe_customer(db, str(customer_id) if customer_id else None)
    if tenant is None:
        tenant_id = _tenant_id_from_metadata(subscription.get("metadata"))
        if tenant_id is not None:
            tenant = await _require_tenant(db, tenant_id)
    if tenant is None:
        logger.error("stripe.subscription_deleted.tenant_not_found", event_id=event_id)
        return

    await _set_billing_fields(
        db,
        tenant,
        billing_status=BILLING_STATUS_CANCELED,
        clear_subscription=True,
        reason="customer.subscription.deleted",
    )
    await plan_service.assign_tenant_plan(
        db,
        tenant_id=tenant.id,
        plan_code=DEFAULT_DOWNGRADE_PLAN,
        actor_user_id=None,
        reason="customer.subscription.deleted",
        source=plan_service.SOURCE_STRIPE,
        metadata={"stripe_event_id": event_id},
    )


async def _on_payment_failed(
    db: AsyncSession,
    invoice: dict[str, Any],
    *,
    event_id: str | None,
) -> None:
    customer_id = invoice.get("customer")
    tenant = await _tenant_by_stripe_customer(db, str(customer_id) if customer_id else None)
    if tenant is None:
        logger.error("stripe.payment_failed.tenant_not_found", event_id=event_id)
        return
    await _set_billing_fields(
        db,
        tenant,
        billing_status=BILLING_STATUS_PAST_DUE,
        reason="invoice.payment_failed",
    )
