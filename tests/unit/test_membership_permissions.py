"""Tests de membership_can y defaults de permisos (Paso 30 Fase A)."""

from uuid import uuid4

from app.core.permissions import membership_can, membership_can_appointment
from app.core.scheduling_defaults import DEFAULT_MEMBERSHIP_PERMISSIONS
from app.models.membership import Membership


def _membership(role: str = "member", permissions: dict | None = None) -> Membership:
    m = Membership(
        user_id=uuid4(),
        tenant_id=uuid4(),
        role=role,
        permissions=permissions or dict(DEFAULT_MEMBERSHIP_PERMISSIONS),
    )
    return m


def test_admin_bypasses_all_appointment_permissions() -> None:
    m = _membership(role="admin", permissions={"appointments": {"view": False}})
    assert membership_can_appointment(m, "view") is True
    assert membership_can_appointment(m, "create") is True


def test_member_view_only_by_default() -> None:
    m = _membership(role="member")
    assert membership_can_appointment(m, "view") is True
    assert membership_can_appointment(m, "create") is False
    assert membership_can_appointment(m, "edit") is False
    assert membership_can_appointment(m, "cancel") is False


def test_member_with_create_permission() -> None:
    m = _membership(
        role="member",
        permissions={
            "appointments": {
                "view": True,
                "create": True,
                "edit": False,
                "cancel": False,
            }
        },
    )
    assert membership_can_appointment(m, "create") is True
    assert membership_can_appointment(m, "edit") is False


def test_viewer_uses_permissions_json_not_clerk() -> None:
    m = _membership(
        role="viewer",
        permissions={
            "appointments": {
                "view": True,
                "create": False,
                "edit": False,
                "cancel": False,
            }
        },
    )
    assert membership_can(m, "appointments", "view") is True
    assert membership_can(m, "appointments", "create") is False


def test_member_with_none_permissions_returns_false() -> None:
    m = _membership(role="member")
    m.permissions = None  # type: ignore[assignment]
    assert membership_can(m, "appointments", "view") is False
    assert membership_can_appointment(m, "view") is False


def test_unknown_module_returns_false() -> None:
    m = _membership(role="member")
    assert membership_can(m, "unknown_module", "view") is False
