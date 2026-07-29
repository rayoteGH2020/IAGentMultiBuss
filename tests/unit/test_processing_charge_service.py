"""Estimación de coste y tiempo del procesado excepcional."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.config import get_settings
from app.services.processing_charge_service import estimate_processing


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-key-for-unit-tests")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://saas:saas@localhost:5432/saas",  # pragma: allowlist secret
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_estimate_scales_linearly_with_pages() -> None:
    one = estimate_processing(1)
    ten = estimate_processing(10)

    assert ten.input_tokens == one.input_tokens * 10
    assert ten.output_tokens == one.output_tokens * 10
    assert ten.seconds == one.seconds * 10
    assert ten.provider_cost_eur > one.provider_cost_eur


def test_estimate_never_reports_zero_pages() -> None:
    assert estimate_processing(0).pages == 1


def test_billable_applies_configured_multiplier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCUMENT_OVERRIDE_CHARGE_MULTIPLIER", "3.0")
    get_settings.cache_clear()

    estimate = estimate_processing(50)
    expected = (estimate.provider_cost_eur * Decimal("3.0")).quantize(Decimal("0.01"))
    assert estimate.billable_eur == expected


def test_estimate_uses_configured_extraction_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL_EXTRACTION", "gemini-2.5-pro")
    get_settings.cache_clear()

    assert estimate_processing(2).model == "gemini-2.5-pro"
