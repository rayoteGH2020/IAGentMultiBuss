"""Tests de schemas scheduling — color de profesional."""

import pytest
from app.schemas.scheduling import (
    DEFAULT_PROFESSIONAL_COLOR,
    ProfessionalCreate,
    ProfessionalRead,
    ProfessionalUpdate,
    sanitize_professional_color,
    validate_professional_color,
)
from pydantic import ValidationError


def test_validate_professional_color_accepts_hex_and_lowercases() -> None:
    assert validate_professional_color("#6366F1") == "#6366f1"


@pytest.mark.parametrize(
    "value",
    [
        "red",
        "#abc",
        "#1234567",
        "#gggggg",
        "javascript:alert(1)",
        "#000000; } body { display:none",
    ],
)
def test_validate_professional_color_rejects_invalid(value: str) -> None:
    with pytest.raises(ValueError, match="6-digit hex"):
        validate_professional_color(value)


def test_sanitize_professional_color_falls_back_to_default() -> None:
    assert sanitize_professional_color("not-a-color") == DEFAULT_PROFESSIONAL_COLOR
    assert sanitize_professional_color("#AABBCC") == "#aabbcc"


def test_professional_create_rejects_invalid_color() -> None:
    with pytest.raises(ValidationError):
        ProfessionalCreate(display_name="Ana", color="bad")


def test_professional_create_normalizes_valid_color() -> None:
    prof = ProfessionalCreate(display_name="Ana", color="#FF00AA")
    assert prof.color == "#ff00aa"


def test_professional_update_optional_color() -> None:
    updated = ProfessionalUpdate(color="#112233")
    assert updated.color == "#112233"
    assert ProfessionalUpdate(color=None).color is None


def test_professional_read_sanitizes_legacy_db_value() -> None:
    from uuid import uuid4

    read = ProfessionalRead(
        id=uuid4(),
        display_name="Legacy",
        user_id=None,
        color="evil-injection",
        is_active=True,
        is_bookable=True,
        sort_order=0,
    )
    assert read.color == DEFAULT_PROFESSIONAL_COLOR
