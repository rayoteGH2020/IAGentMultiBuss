"""Rate limiter para subidas de documentos de conocimiento (Paso 18).

Usa el patrón Redis INCRBY + TTL (igual que chat_service.py para mensajes):
- Clave diaria por tenant: ``rate:knowledge_upload:{tenant_id}:{yyyy-mm-dd}``.
- INCRBY especulativo: si supera el límite se hace DECRBY para revertir.
- El TTL se establece solo en la primera subida del día (count == n_files).

Este módulo no conoce HTTP ni FastAPI; solo trabaja con el cliente Redis.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from app.core.errors import RateLimitError

logger = structlog.get_logger(__name__)

_RATE_KEY_TEMPLATE = "rate:knowledge_upload:{tenant_id}:{date}"
_DAY_SECONDS: int = 86400


async def check_knowledge_upload_rate(
    redis: Any,
    *,
    tenant_id: UUID,
    max_per_day: int,
    n_files: int = 1,
) -> None:
    """Verifica y contabiliza la cuota de subidas de conocimiento del tenant.

    Incrementa especulativamente el contador; si supera el límite lo revierte
    y lanza ``RateLimitError`` con el mensaje visible en la UI.

    Args:
        redis: Cliente ``redis.asyncio.Redis`` ya conectado.
        tenant_id: Tenant al que se imputa la cuota.
        max_per_day: Máximo de ficheros que puede subir el tenant en un día.
        n_files: Número de ficheros que se intenta subir en esta petición.

    Raises:
        RateLimitError: si añadir ``n_files`` superaría ``max_per_day``.
    """
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    key = _RATE_KEY_TEMPLATE.format(tenant_id=tenant_id, date=date_str)

    new_count = int(await redis.incrby(key, n_files))

    # Primer uso del día: establece el TTL para que la clave expire al día siguiente.
    if new_count == n_files:
        await redis.expire(key, _DAY_SECONDS)

    if new_count > max_per_day:
        # Revierte el incremento especulativo para no contaminar el contador.
        await redis.decrby(key, n_files)
        logger.warning(
            "knowledge.upload.rate_limit",
            tenant_id=str(tenant_id),
            n_files=n_files,
            max_per_day=max_per_day,
        )
        raise RateLimitError(
            f"Límite diario de subidas alcanzado ({max_per_day} documentos/día). "
            "Inténtalo de nuevo mañana."
        )
