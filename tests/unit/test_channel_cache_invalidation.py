"""Tests de invalidación de caché semántica de canal."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.services import channel_chat_service


@pytest.mark.asyncio
async def test_invalidate_response_cache_for_tenant_deletes_rows() -> None:
    db = AsyncMock()
    result = MagicMock()
    result.rowcount = 3
    db.execute = AsyncMock(return_value=result)

    deleted = await channel_chat_service.invalidate_response_cache_for_tenant(
        db,
        tenant_id=uuid4(),
    )
    assert deleted == 3
    db.execute.assert_awaited_once()
