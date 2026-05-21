"""Humo del cliente LLM contra API Anthropic real (skip sin claves)."""

from collections.abc import Awaitable, Callable

import pytest
from app.config import get_settings
from app.core.db import set_tenant_context
from app.llm import get_llm_client, render_prompt
from app.llm.client import reset_llm_client_for_tests
from app.models import LLMCall, Tenant
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


class Greeting(BaseModel):
    saludo: str
    idioma: str


async def test_llm_client_classify_smoke(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Awaitable[Tenant]],
    llm_calls_schema_ready: None,
) -> None:
    if not get_settings().anthropic_api_key.get_secret_value():
        pytest.skip(
            "Falta ANTHROPIC_API_KEY; lanzar pytest con Infisical, p. ej. "
            "`infisical run -- uv run pytest ...`.",
        )
    reset_llm_client_for_tests()
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    prompt = render_prompt("ping_v1", name="Ana")
    client = get_llm_client()
    completion = await client.complete(
        task="classify",
        messages=[{"role": "user", "content": prompt}],
        response_model=Greeting,
        tenant_id=tenant.id,
        prompt_version="ping_v1",
        db=db_session,
    )
    await db_session.commit()

    assert completion.result.saludo
    assert completion.result.idioma
    assert completion.llm_call_id

    rows = (await db_session.execute(select(LLMCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "ok"
    assert rows[0].input_tokens > 0
    assert rows[0].cost_eur > 0
