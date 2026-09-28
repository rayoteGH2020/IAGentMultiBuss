"""Jobs programados de memberships: bajas con fecha efectiva vencida."""

from __future__ import annotations

from typing import Any

import structlog

from app.core.db import session_scope
from app.services import membership_service

log = structlog.get_logger(__name__)


async def expire_member_removals(ctx: dict[str, Any]) -> int:
    """Desactiva memberships cuya baja ya es efectiva (cron ARQ).

    El middleware corta el acceso en la primera petición tras la fecha; este
    job lo hace también para usuarios que no vuelven a entrar, de modo que la
    BD y ``audit_log`` reflejan la baja en la fecha comprometida.
    """
    async with session_scope() as db:
        executed = await membership_service.execute_due_removals(db)
    log.info("membership.expire_removals_done", executed=executed)
    return executed
