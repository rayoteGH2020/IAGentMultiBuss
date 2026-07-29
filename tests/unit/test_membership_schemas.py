"""Tests de schemas membership (Paso 30 Fase B)."""

from app.schemas.membership import (
    AppointmentPermissions,
    MembershipPermissions,
    TenantMemberCreate,
)


def test_tenant_member_create_normalizes_email() -> None:
    member = TenantMemberCreate(email="  User@Example.COM ", name="Test User")
    assert member.email == "user@example.com"


def test_membership_permissions_default_view_only() -> None:
    perms = MembershipPermissions()
    assert perms.appointments.view is True
    assert perms.appointments.create is False


def test_appointment_permissions_roundtrip() -> None:
    perms = MembershipPermissions(
        appointments=AppointmentPermissions(view=True, create=True, edit=False, cancel=False)
    )
    restored = MembershipPermissions.from_json_dict(perms.to_json_dict())
    assert restored.appointments.create is True
