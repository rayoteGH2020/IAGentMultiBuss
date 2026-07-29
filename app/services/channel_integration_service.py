"""Channel integration service — save, lookup and decrypt channel credentials (Paso 21 C)."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.core.crypto import decrypt_token as _crypto_decrypt
from app.core.crypto import encrypt_token
from app.core.errors import NotFoundError, ValidationError
from app.models.channel_integration import ChannelIntegration, ChannelType
from app.models.tenant import Tenant
from app.schemas.channel import ChannelIntegrationRead, ChannelIntegrationStatus
from app.schemas.tenant import TenantRead

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

_WA_PHONE_UNIQUE_INDEX = "uq_channel_integrations_wa_phone_active"


def _require_encryption_key() -> str:
    key = get_settings().encryption_key.get_secret_value().strip()
    if not key:
        raise ValidationError("ENCRYPTION_KEY is not configured")
    return key


def _normalize_phone_number_id(phone_number_id: str | None) -> str | None:
    if phone_number_id is None:
        return None
    normalized = phone_number_id.strip()
    return normalized or None


async def _enable_webhook_lookup(db: AsyncSession) -> None:
    """Set session flag that activates the webhook_select RLS policy.

    The flag is local to the current transaction (is_local=True) and reverts
    automatically on commit/rollback, so it never leaks to the connection pool.
    Required because saas_app has NOBYPASSRLS and webhook handlers need to look
    up tenants without knowing the tenant_id upfront.
    """
    await db.execute(text("SELECT set_config('app.webhook_lookup', 'true', true)"))


async def _assert_whatsapp_phone_number_id_available(
    db: AsyncSession,
    *,
    phone_number_id: str,
    exclude_integration_id: UUID | None,
) -> None:
    """Reject if another active WhatsApp integration already owns this PNID."""
    await _enable_webhook_lookup(db)
    stmt = (
        select(ChannelIntegration.id, ChannelIntegration.tenant_id)
        .where(ChannelIntegration.phone_number_id == phone_number_id)
        .where(ChannelIntegration.channel == ChannelType.whatsapp.value)
        .where(ChannelIntegration.status == ChannelIntegrationStatus.active.value)
    )
    if exclude_integration_id is not None:
        stmt = stmt.where(ChannelIntegration.id != exclude_integration_id)
    conflict = (await db.execute(stmt)).first()
    if conflict is not None:
        logger.warning(
            "channel.whatsapp.phone_number_id_conflict",
            phone_number_id=phone_number_id,
            conflicting_tenant_id=str(conflict.tenant_id),
        )
        raise ValidationError(
            "Este Phone Number ID de WhatsApp ya está en uso por otra organización."
        )


async def get_integration(
    db: AsyncSession,
    tenant_id: UUID,
    channel: str,
) -> ChannelIntegration | None:
    """Return the active integration for a tenant+channel (RLS enforced)."""
    stmt = select(ChannelIntegration).where(
        ChannelIntegration.tenant_id == tenant_id,
        ChannelIntegration.channel == channel,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def save_integration(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    channel: str,
    api_token: str,
    phone_number_id: str | None = None,
    display_name: str | None = None,
    confidence_threshold: float = 0.5,
) -> tuple[ChannelIntegration, str | None]:
    """Upsert a channel integration with encrypted credentials.

    Returns (integration, webhook_secret) where webhook_secret is a freshly
    generated plain-text secret for Telegram's setWebhook call, or None for
    WhatsApp (which does not use a per-integration webhook secret).
    """
    settings = get_settings()
    if (
        channel == "whatsapp"
        and settings.app_env in ("staging", "production")
        and not settings.whatsapp_app_secret.get_secret_value().strip()
    ):
        raise ValidationError(
            "WHATSAPP_APP_SECRET must be configured before saving WhatsApp "
            "integrations in staging/production"
        )

    normalized_phone = _normalize_phone_number_id(phone_number_id)
    if channel == ChannelType.whatsapp.value and normalized_phone is None:
        raise ValidationError("El Phone Number ID de WhatsApp es obligatorio.")

    enc_key = _require_encryption_key()
    api_token_enc = encrypt_token(api_token, enc_key)

    plain_webhook_secret: str | None = None
    webhook_secret_enc: bytes | None = None
    if channel == "telegram":
        plain_webhook_secret = secrets.token_hex(32)
        webhook_secret_enc = encrypt_token(plain_webhook_secret, enc_key)

    integration = await get_integration(db, tenant_id, channel)
    if integration is None:
        integration = ChannelIntegration(tenant_id=tenant_id, channel=channel)
        db.add(integration)

    if channel == ChannelType.whatsapp.value and normalized_phone is not None:
        await _assert_whatsapp_phone_number_id_available(
            db,
            phone_number_id=normalized_phone,
            exclude_integration_id=integration.id if integration.id else None,
        )

    integration.status = ChannelIntegrationStatus.active.value
    integration.api_token_enc = api_token_enc
    integration.phone_number_id = normalized_phone
    integration.display_name = display_name
    integration.confidence_threshold = confidence_threshold
    if webhook_secret_enc is not None:
        integration.webhook_secret_enc = webhook_secret_enc

    try:
        # SAVEPOINT: si el unique parcial dispara, la sesión sigue usable para el UI.
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        detail = str(getattr(exc, "orig", None) or exc).lower()
        if _WA_PHONE_UNIQUE_INDEX in detail or "phone_number_id" in detail:
            logger.warning(
                "channel.whatsapp.phone_number_id_unique_violation",
                phone_number_id=normalized_phone,
                tenant_id=str(tenant_id),
            )
            raise ValidationError(
                "Este Phone Number ID de WhatsApp ya está en uso por otra organización."
            ) from exc
        raise
    logger.info(
        "channel.integration.saved",
        tenant_id=str(tenant_id),
        channel=channel,
        integration_id=str(integration.id),
    )
    return integration, plain_webhook_secret


async def revoke_integration(
    db: AsyncSession,
    tenant_id: UUID,
    channel: str,
) -> None:
    """Mark a channel integration as revoked and erase stored credentials."""
    integration = await get_integration(db, tenant_id, channel)
    if integration is None:
        raise NotFoundError(f"Channel integration not found: {channel}")

    integration.status = ChannelIntegrationStatus.revoked.value
    integration.api_token_enc = None
    integration.webhook_secret_enc = None
    await db.flush()
    logger.info(
        "channel.integration.revoked",
        tenant_id=str(tenant_id),
        channel=channel,
        integration_id=str(integration.id),
    )


async def get_integration_by_phone_number_id(
    db: AsyncSession,
    phone_number_id: str,
) -> ChannelIntegration | None:
    """Cross-tenant lookup for the WhatsApp webhook handler (returns integration).

    Activates the webhook_select RLS policy (transaction-local flag).
    Only call from get_db_no_tenant sessions (webhook endpoints).

    Fail-closed: if more than one active row matches (pre-unique-index legacy
    data or race), log critically and return None so the webhook does not
    enqueue to a wrong tenant.
    """
    await _enable_webhook_lookup(db)
    stmt = (
        select(ChannelIntegration)
        .where(ChannelIntegration.phone_number_id == phone_number_id)
        .where(ChannelIntegration.channel == ChannelType.whatsapp.value)
        .where(ChannelIntegration.status == ChannelIntegrationStatus.active.value)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    if not rows:
        return None
    if len(rows) > 1:
        logger.critical(
            "channel.whatsapp.ambiguous_phone_number_id",
            phone_number_id=phone_number_id,
            match_count=len(rows),
            tenant_ids=[str(row.tenant_id) for row in rows],
            integration_ids=[str(row.id) for row in rows],
        )
        return None
    return rows[0]


async def get_tenant_by_phone_number_id(
    db: AsyncSession,
    phone_number_id: str,
) -> Tenant | None:
    """Cross-tenant lookup for the WhatsApp webhook handler.

    Activates the webhook_select RLS policy (transaction-local flag) to allow
    reading channel_integrations without a known tenant context.
    Only call from get_db_no_tenant sessions (webhook endpoints).
    """
    integration = await get_integration_by_phone_number_id(db, phone_number_id)
    if integration is None:
        return None
    # tenants table has no RLS — safe to query without tenant context
    tenant_stmt = select(Tenant).where(Tenant.id == integration.tenant_id)
    tenant_result = await db.execute(tenant_stmt)
    return tenant_result.scalar_one_or_none()


async def get_integration_by_id(
    db: AsyncSession,
    integration_id: UUID,
) -> ChannelIntegration | None:
    """Cross-tenant lookup for the Telegram webhook handler (integration_id in URL path).

    Activates the webhook_select RLS policy (transaction-local flag).
    Only call from get_db_no_tenant sessions (webhook endpoints).
    """
    await _enable_webhook_lookup(db)
    stmt = select(ChannelIntegration).where(
        ChannelIntegration.id == integration_id,
        ChannelIntegration.status == ChannelIntegrationStatus.active.value,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def decrypt_api_token(integration: ChannelIntegration) -> str:
    """Decrypt the stored API token (WhatsApp access token or Telegram bot token)."""
    if not integration.api_token_enc:
        raise ValidationError("Missing encrypted API token")
    return _crypto_decrypt(integration.api_token_enc, _require_encryption_key())


def decrypt_webhook_secret(integration: ChannelIntegration) -> str:
    """Decrypt the stored Telegram webhook secret."""
    if not integration.webhook_secret_enc:
        raise ValidationError("Missing encrypted webhook secret")
    return _crypto_decrypt(integration.webhook_secret_enc, _require_encryption_key())


def _integration_to_read(integration: ChannelIntegration | None) -> ChannelIntegrationRead | None:
    if integration is None:
        return None
    return ChannelIntegrationRead.model_validate(integration)


async def list_tenants_for_admin(db: AsyncSession) -> list[TenantRead]:
    """Lista tenants para el panel superadmin (tabla sin RLS)."""
    stmt = select(Tenant).order_by(Tenant.name)
    result = await db.execute(stmt)
    return [TenantRead.model_validate(t) for t in result.scalars().all()]


async def get_tenant_for_admin(db: AsyncSession, tenant_id: UUID) -> TenantRead:
    """Carga un tenant por id para operaciones admin."""
    stmt = select(Tenant).where(Tenant.id == tenant_id)
    tenant = (await db.execute(stmt)).scalar_one_or_none()
    if tenant is None:
        raise NotFoundError(f"Tenant {tenant_id} not found")
    return TenantRead.model_validate(tenant)


async def build_admin_integrations_context(
    db: AsyncSession,
    tenant: TenantRead,
) -> dict[str, object]:
    """Contexto Jinja para tarjetas WhatsApp/Telegram de un tenant."""
    wa = _integration_to_read(await get_integration(db, tenant.id, "whatsapp"))
    tg = _integration_to_read(await get_integration(db, tenant.id, "telegram"))
    return {
        "tenant": tenant,
        "whatsapp_integration": wa,
        "whatsapp_connected": wa is not None and wa.status == ChannelIntegrationStatus.active.value,
        "telegram_integration": tg,
        "telegram_connected": tg is not None and tg.status == ChannelIntegrationStatus.active.value,
    }
