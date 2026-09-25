"""Tests de is_platform_superadmin: org SADM + rol admin + allowlist."""

from uuid import uuid4

import pytest
from app.core.permissions import is_platform_superadmin
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.user import User

ADMIN_ORG = "org_sadm_123"


def _tenant(clerk_org_id: str = ADMIN_ORG) -> Tenant:
    tenant = Tenant(clerk_org_id=clerk_org_id, name="SADM", plan="free", settings={})
    tenant.id = uuid4()
    return tenant


def _membership(role: str = "admin", *, is_active: bool = True) -> Membership:
    membership = Membership(
        user_id=uuid4(),
        tenant_id=uuid4(),
        role=role,
        permissions={},
    )
    membership.is_active = is_active
    return membership


def _user(clerk_user_id: str = "user_sadm") -> User:
    user = User(clerk_user_id=clerk_user_id, email="sadm@test.local", name="SADM")
    user.id = uuid4()
    return user


def test_admin_of_sadm_org_is_superadmin() -> None:
    assert (
        is_platform_superadmin(
            tenant=_tenant(),
            membership=_membership("admin"),
            user=_user(),
            admin_clerk_org_id=ADMIN_ORG,
        )
        is True
    )


def test_member_of_sadm_org_is_not_superadmin() -> None:
    for role in ("member", "viewer"):
        assert (
            is_platform_superadmin(
                tenant=_tenant(),
                membership=_membership(role),
                user=_user(),
                admin_clerk_org_id=ADMIN_ORG,
            )
            is False
        )


def test_inactive_admin_membership_is_not_superadmin() -> None:
    assert (
        is_platform_superadmin(
            tenant=_tenant(),
            membership=_membership("admin", is_active=False),
            user=_user(),
            admin_clerk_org_id=ADMIN_ORG,
        )
        is False
    )


def test_admin_of_other_org_is_not_superadmin() -> None:
    assert (
        is_platform_superadmin(
            tenant=_tenant("org_customer_999"),
            membership=_membership("admin"),
            user=_user(),
            admin_clerk_org_id=ADMIN_ORG,
        )
        is False
    )


def test_empty_admin_org_disables_superadmin() -> None:
    assert (
        is_platform_superadmin(
            tenant=_tenant(),
            membership=_membership("admin"),
            user=_user(),
            admin_clerk_org_id="   ",
        )
        is False
    )


def test_missing_session_context_is_not_superadmin() -> None:
    assert (
        is_platform_superadmin(
            tenant=None,
            membership=_membership("admin"),
            user=_user(),
            admin_clerk_org_id=ADMIN_ORG,
        )
        is False
    )
    assert (
        is_platform_superadmin(
            tenant=_tenant(),
            membership=None,
            user=_user(),
            admin_clerk_org_id=ADMIN_ORG,
        )
        is False
    )


def test_allowlist_restricts_to_listed_users() -> None:
    allowed = frozenset({"user_ruben"})
    assert (
        is_platform_superadmin(
            tenant=_tenant(),
            membership=_membership("admin"),
            user=_user("user_ruben"),
            admin_clerk_org_id=ADMIN_ORG,
            allowed_clerk_user_ids=allowed,
        )
        is True
    )
    assert (
        is_platform_superadmin(
            tenant=_tenant(),
            membership=_membership("admin"),
            user=_user("user_otro"),
            admin_clerk_org_id=ADMIN_ORG,
            allowed_clerk_user_ids=allowed,
        )
        is False
    )
    assert (
        is_platform_superadmin(
            tenant=_tenant(),
            membership=_membership("admin"),
            user=None,
            admin_clerk_org_id=ADMIN_ORG,
            allowed_clerk_user_ids=allowed,
        )
        is False
    )


def test_settings_parses_allowlist_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import Settings

    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("SUPERADMIN_CLERK_USER_IDS", " user_a , user_b ,, ")

    settings = Settings()

    assert settings.superadmin_clerk_user_id_set == frozenset({"user_a", "user_b"})
