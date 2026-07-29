"""Humo del cliente LLM contra API real (skip sin claves o créditos)."""

from collections.abc import Awaitable, Callable

import pytest
from app.config import get_settings
from app.core.db import set_tenant_context
from app.core.errors import LLMCompleteError
from app.llm import get_llm_client, render_prompt
from app.llm.client import reset_llm_client_for_tests
from app.models import LLMCall, Tenant
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.integration, pytest.mark.real_llm]


class Greeting(BaseModel):
    saludo: str
    idioma: str


def _skip_if_provider_key_missing(provider: str) -> None:
    settings = get_settings()
    if provider == "anthropic" and not settings.anthropic_api_key.get_secret_value():
        pytest.skip(
            "Falta ANTHROPIC_API_KEY para classify con modelo Anthropic; "
            "usa Infisical o revisa LLM_MODEL_CLASSIFY.",
        )
    if provider == "google" and not settings.google_api_key.get_secret_value():
        pytest.skip(
            "Falta GOOGLE_API_KEY para classify con modelo Gemini; "
            "usa Infisical o revisa LLM_MODEL_CLASSIFY.",
        )


def _skip_if_billing_or_quota_error(exc: LLMCompleteError) -> None:
    msg = str(exc).lower()
    if any(token in msg for token in ("429", "resource_exhausted", "depleted", "quota")):
        pytest.skip(f"Proveedor LLM sin cuota/créditos disponibles: {exc}")


async def test_llm_client_classify_smoke(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Awaitable[Tenant]],
    llm_calls_schema_ready: None,
) -> None:
    get_settings.cache_clear()
    reset_llm_client_for_tests()

    client = get_llm_client()
    _model, provider = client._resolve_model("classify")
    _skip_if_provider_key_missing(provider)

    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    prompt = render_prompt("ping_v1", name="Ana")
    try:
        completion = await client.complete(
            task="classify",
            messages=[{"role": "user", "content": prompt}],
            response_model=Greeting,
            tenant_id=tenant.id,
            prompt_version="ping_v1",
            db=db_session,
        )
    except LLMCompleteError as exc:
        _skip_if_billing_or_quota_error(exc)
        raise

    await db_session.commit()

    assert completion.result.saludo
    assert completion.result.idioma
    assert completion.llm_call_id

    # Tras commit, set_config(is_local=true) se revierte; hay que restablecer RLS.
    await set_tenant_context(db_session, str(tenant.id))
    row = (
        await db_session.execute(select(LLMCall).where(LLMCall.id == completion.llm_call_id))
    ).scalar_one()

    assert row.status == "ok"
    assert row.input_tokens > 0
    assert row.cost_eur > 0
