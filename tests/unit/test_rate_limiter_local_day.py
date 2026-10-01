"""Cuotas diarias: el día se reinicia a las 00:00 hora de España, no UTC."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core import rate_limiter
from app.core.rate_limiter import daily_ttl_seconds, local_day_key

_MARGIN = 3600


@pytest.fixture(autouse=True)
def _madrid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fija la zona de la app: el test no depende de APP_DISPLAY_TIMEZONE del entorno."""
    from zoneinfo import ZoneInfo

    monkeypatch.setattr(rate_limiter, "resolve_display_timezone", lambda: ZoneInfo("Europe/Madrid"))


@pytest.mark.parametrize(
    ("utc_now", "expected_day"),
    [
        # Verano (UTC+2): 22:30 UTC ya es el día siguiente en España.
        (datetime(2026, 7, 14, 22, 30, tzinfo=UTC), "2026-07-15"),
        (datetime(2026, 7, 14, 21, 59, tzinfo=UTC), "2026-07-14"),
        # Invierno (UTC+1): 23:00 UTC ya es el día siguiente.
        (datetime(2026, 1, 10, 23, 0, tzinfo=UTC), "2026-01-11"),
        (datetime(2026, 1, 10, 22, 59, tzinfo=UTC), "2026-01-10"),
    ],
)
def test_local_day_key_changes_at_spanish_midnight(utc_now: datetime, expected_day: str) -> None:
    assert local_day_key(utc_now) == expected_day


@pytest.mark.parametrize(
    ("utc_now", "seconds_to_midnight"),
    [
        # 20:00 local en verano → 4 h.
        (datetime(2026, 7, 14, 18, 0, tzinfo=UTC), 4 * 3600),
        # 00:00 local en invierno → día completo de 24 h.
        (datetime(2026, 1, 10, 23, 0, tzinfo=UTC), 24 * 3600),
        # 29/03/2026 (cambio a verano): el día dura 23 h.
        (datetime(2026, 3, 28, 23, 0, tzinfo=UTC), 23 * 3600),
        # 25/10/2026 (cambio a invierno): el día dura 25 h.
        (datetime(2026, 10, 24, 22, 0, tzinfo=UTC), 25 * 3600),
    ],
)
def test_daily_ttl_lasts_until_local_midnight_even_on_dst_days(
    utc_now: datetime, seconds_to_midnight: int
) -> None:
    assert daily_ttl_seconds(utc_now) == seconds_to_midnight + _MARGIN


@pytest.mark.asyncio
async def test_daily_quota_keys_use_local_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """Los límites diarios que quedan (documentos, conocimiento, chat) usan el día local."""
    monkeypatch.setattr(rate_limiter, "local_day_key", lambda now=None: "2026-07-15")
    monkeypatch.setattr(rate_limiter, "daily_ttl_seconds", lambda now=None: 1234)
    redis = AsyncMock()
    redis.incrby = AsyncMock(return_value=1)
    tenant_id, user_id = uuid4(), uuid4()

    await rate_limiter.check_documents_upload_rate(redis, tenant_id=tenant_id, max_per_day=5)
    await rate_limiter.check_knowledge_upload_rate(redis, tenant_id=tenant_id, max_per_day=5)
    await rate_limiter.check_chat_messages_rate(
        redis, tenant_id=tenant_id, user_id=user_id, max_per_day=5, max_per_user_day=5
    )

    keys = [call.args[0] for call in redis.incrby.await_args_list]
    assert keys == [
        f"rate:documents_upload:{tenant_id}:2026-07-15",
        f"rate:knowledge_upload:{tenant_id}:2026-07-15",
        f"rate:chat_messages:{tenant_id}:{user_id}:2026-07-15",
        f"rate:chat_messages:{tenant_id}:2026-07-15",
    ]
    assert {call.args[1] for call in redis.expire.await_args_list} == {1234}
