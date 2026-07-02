"""Admin routes for managing channel integrations per tenant (Paso 21 D).

Only accessible by the platform superadmin (ADMIN_CLERK_ORG_ID in config).
Superadmin configures WhatsApp and Telegram integrations on behalf of each tenant.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import telegram_client
from app.core.db import set_tenant_context
from app.core.errors import AppError, NotFoundError
from app.core.templating import render
from app.deps import SuperAdmin, get_db_no_tenant
from app.models.channel_integration import ChannelIntegrationStatus
from app.models.tenant import Tenant
from app.services import channel_integration_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/integrations", tags=["admin-integrations"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_target_tenant(db: AsyncSession, tenant_id: UUID) -> Tenant:
    """Load a tenant by id (tenants table has no RLS — safe from no-tenant session)."""
    stmt = select(Tenant).where(Tenant.id == tenant_id)
    result = await db.execute(stmt)
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise NotFoundError(f"Tenant {tenant_id} not found")
    return tenant


async def _channel_ctx(db: AsyncSession, tenant: Tenant) -> dict[str, object]:
    """Build context for both integration cards of a tenant."""
    wa = await channel_integration_service.get_integration(db, tenant.id, "whatsapp")
    tg = await channel_integration_service.get_integration(db, tenant.id, "telegram")
    return {
        "tenant": tenant,
        "whatsapp_integration": wa,
        "whatsapp_connected": wa is not None and wa.status == ChannelIntegrationStatus.active.value,
        "telegram_integration": tg,
        "telegram_connected": tg is not None and tg.status == ChannelIntegrationStatus.active.value,
    }


# ---------------------------------------------------------------------------
# List — all tenants
# ---------------------------------------------------------------------------


@router.get("")
async def admin_integrations_list(
    request: Request,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    stmt = select(Tenant).order_by(Tenant.name)
    result = await db.execute(stmt)
    tenants = result.scalars().all()
    return render(
        request,
        full="pages/admin/channel_integrations_list.html",
        partial="pages/admin/channel_integrations_list.html",
        ctx={"tenants": tenants},
    )


# ---------------------------------------------------------------------------
# Detail — manage integrations for a specific tenant
# ---------------------------------------------------------------------------


@router.get("/{tenant_id}")
async def admin_integrations_detail(
    request: Request,
    tenant_id: UUID,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    tenant = await _get_target_tenant(db, tenant_id)
    await set_tenant_context(db, str(tenant_id))
    ctx = await _channel_ctx(db, tenant)
    return render(
        request,
        full="pages/admin/channel_integrations_detail.html",
        partial="pages/admin/channel_integrations_detail.html",
        ctx=ctx,
    )


# ---------------------------------------------------------------------------
# WhatsApp — save
# ---------------------------------------------------------------------------


@router.post("/{tenant_id}/whatsapp/save")
async def admin_whatsapp_save(
    request: Request,
    tenant_id: UUID,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
    phone_number_id: Annotated[str, Form()] = "",
    api_token: Annotated[str, Form()] = "",
    display_name: Annotated[str, Form()] = "",
    confidence_threshold: Annotated[float, Form()] = 0.5,
) -> HTMLResponse:
    tenant = await _get_target_tenant(db, tenant_id)
    await set_tenant_context(db, str(tenant_id))
    error: str | None = None
    try:
        await channel_integration_service.save_integration(
            db,
            tenant_id=tenant_id,
            channel="whatsapp",
            api_token=api_token.strip(),
            phone_number_id=phone_number_id.strip() or None,
            display_name=display_name.strip() or None,
            confidence_threshold=confidence_threshold,
        )
        logger.info("admin.whatsapp.saved", tenant_id=str(tenant_id))
    except AppError as exc:
        error = exc.message
    ctx = await _channel_ctx(db, tenant)
    ctx["error_message"] = error
    return render(
        request,
        full="components/integration_whatsapp.html",
        partial="components/integration_whatsapp.html",
        ctx=ctx,
    )


# ---------------------------------------------------------------------------
# WhatsApp — disconnect
# ---------------------------------------------------------------------------


@router.post("/{tenant_id}/whatsapp/disconnect")
async def admin_whatsapp_disconnect(
    request: Request,
    tenant_id: UUID,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    tenant = await _get_target_tenant(db, tenant_id)
    await set_tenant_context(db, str(tenant_id))
    error: str | None = None
    try:
        await channel_integration_service.revoke_integration(db, tenant_id, "whatsapp")
        logger.info("admin.whatsapp.revoked", tenant_id=str(tenant_id))
    except (AppError, NotFoundError) as exc:
        error = exc.message
    ctx = await _channel_ctx(db, tenant)
    ctx["error_message"] = error
    return render(
        request,
        full="components/integration_whatsapp.html",
        partial="components/integration_whatsapp.html",
        ctx=ctx,
    )


# ---------------------------------------------------------------------------
# Telegram — save
# ---------------------------------------------------------------------------


@router.post("/{tenant_id}/telegram/save")
async def admin_telegram_save(
    request: Request,
    tenant_id: UUID,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
    api_token: Annotated[str, Form()] = "",
    display_name: Annotated[str, Form()] = "",
    confidence_threshold: Annotated[float, Form()] = 0.5,
) -> HTMLResponse:
    tenant = await _get_target_tenant(db, tenant_id)
    await set_tenant_context(db, str(tenant_id))
    error: str | None = None
    try:
        integration, plain_webhook_secret = await channel_integration_service.save_integration(
            db,
            tenant_id=tenant_id,
            channel="telegram",
            api_token=api_token.strip(),
            display_name=display_name.strip() or None,
            confidence_threshold=confidence_threshold,
        )
        # Registrar el webhook en Telegram con el secret generado
        if plain_webhook_secret:
            settings = get_settings()
            webhook_url = f"{settings.app_base_url}/api/webhooks/telegram/{integration.id}"
            try:
                await telegram_client.set_webhook(
                    api_token.strip(),
                    webhook_url,
                    webhook_secret=plain_webhook_secret,
                )
            except Exception:
                logger.warning(
                    "admin.telegram.set_webhook_failed",
                    tenant_id=str(tenant_id),
                    integration_id=str(integration.id),
                )
        logger.info(
            "admin.telegram.saved",
            tenant_id=str(tenant_id),
            integration_id=str(integration.id),
        )
    except AppError as exc:
        error = exc.message
    ctx = await _channel_ctx(db, tenant)
    ctx["error_message"] = error
    return render(
        request,
        full="components/integration_telegram.html",
        partial="components/integration_telegram.html",
        ctx=ctx,
    )


# ---------------------------------------------------------------------------
# Telegram — disconnect
# ---------------------------------------------------------------------------


@router.post("/{tenant_id}/telegram/disconnect")
async def admin_telegram_disconnect(
    request: Request,
    tenant_id: UUID,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    tenant = await _get_target_tenant(db, tenant_id)
    await set_tenant_context(db, str(tenant_id))
    error: str | None = None
    try:
        # Intentar deleteWebhook antes de revocar (necesitamos el token antes de borrarlo)
        existing = await channel_integration_service.get_integration(db, tenant_id, "telegram")
        if existing and existing.api_token_enc:
            try:
                bot_token = channel_integration_service.decrypt_api_token(existing)
                await telegram_client.delete_webhook(bot_token)
            except Exception:
                logger.warning("admin.telegram.delete_webhook_failed", tenant_id=str(tenant_id))
        await channel_integration_service.revoke_integration(db, tenant_id, "telegram")
        logger.info("admin.telegram.revoked", tenant_id=str(tenant_id))
    except (AppError, NotFoundError) as exc:
        error = exc.message
    ctx = await _channel_ctx(db, tenant)
    ctx["error_message"] = error
    return render(
        request,
        full="components/integration_telegram.html",
        partial="components/integration_telegram.html",
        ctx=ctx,
    )
