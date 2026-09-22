"""Normalización de roles org Clerk (JWT v1/v2 y webhooks)."""

from app.services.auth_service import normalize_org_role, org_id_from_claims, org_role_from_claims


def test_normalize_org_role_strips_prefix_and_validates() -> None:
    assert normalize_org_role("org:admin") == "admin"
    assert normalize_org_role("org:member") == "member"
    assert normalize_org_role("org:viewer") == "viewer"
    assert normalize_org_role("admin") == "admin"
    assert normalize_org_role("org:unknown") == "member"
    assert normalize_org_role("billing") == "member"


def test_org_role_from_claims_v1_and_v2() -> None:
    assert org_role_from_claims({"org_role": "org:admin"}) == "admin"
    assert org_role_from_claims({"o": {"rol": "admin"}}) == "admin"
    assert org_role_from_claims({}) == "member"


def test_org_id_from_claims_v1_and_v2() -> None:
    assert org_id_from_claims({"org_id": "org_abc"}) == "org_abc"
    assert org_id_from_claims({"o": {"id": "org_xyz"}}) == "org_xyz"
    assert org_id_from_claims({}) is None
