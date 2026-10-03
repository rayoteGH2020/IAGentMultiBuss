"""Crons ARQ de retención: ``activity_log`` (D029) y ``audit_log`` (P2c-7)."""

from __future__ import annotations

from typing import Any

import structlog

from app.config import get_settings
from app.services import activity_log_service, audit_retention_service

logger = structlog.get_logger(__name__)


async def purge_activity_log(ctx: dict[str, Any]) -> dict[str, Any]:
    """Borra las filas más antiguas que ``ACTIVITY_LOG_RETENTION_DAYS`` (0 = no purgar)."""
    _ = ctx
    retention_days = get_settings().activity_log_retention_days
    if retention_days == 0:
        return {"status": "skipped"}
    deleted = await activity_log_service.purge_expired(retention_days)
    logger.info("activity.purged", deleted_count=deleted, retention_days=retention_days)
    return {"status": "ok", "deleted_count": deleted}


async def purge_audit_log(ctx: dict[str, Any]) -> dict[str, Any]:
    """Borra las entradas más antiguas que ``AUDIT_LOG_RETENTION_DAYS`` (0 = no purgar)."""
    _ = ctx
    retention_days = get_settings().audit_log_retention_days
    if retention_days == 0:
        return {"status": "skipped"}
    deleted = await audit_retention_service.purge_expired(retention_days)
    logger.info("audit.purged", deleted_count=deleted, retention_days=retention_days)
    return {"status": "ok", "deleted_count": deleted}
