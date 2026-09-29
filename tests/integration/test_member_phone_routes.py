"""Teléfono en la ficha de miembro: solo el admin lo cambia; el co_admin lo ve (D020)."""

# F811: la fixture client_with_auth se importa para reutilizarla y pytest la
# inyecta con el mismo nombre en cada test.
# ruff: noqa: F811

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.schemas.membership import MembershipPermissions, TenantMemberRead

from tests.integration.scheduling_test_helpers import auth_headers, csrf_headers
from tests.integration.test_appointments_routes_permissions import (  # noqa: F401 (fixture)
    client_with_auth,
)

pytestmark = pytest.mark.integration

_ALL_PERMS = {"appointments": {"view": True, "create": True, "edit": True, "cancel": True}}


def _member(phone: str | None = None) -> TenantMemberRead:
    return TenantMemberRead(
        membership_id=uuid4(),
        user_id=uuid4(),
        email="miembro@example.com",
        name="Miembro",
        phone=phone,
        role="member",
        permissions=MembershipPermissions(),
    )


def _post(client: object, auth: object, membership_id: object, data: dict[str, str]) -> object:
    return client.post(  # type: ignore[attr-defined]
        f"/settings/members/{membership_id}",
        data=data,
        headers={
            **auth_headers(),
            **csrf_headers(auth.user, auth.tenant),  # type: ignore[attr-defined]
            "HX-Request": "true",
        },
    )


@pytest.mark.parametrize(("role", "editable"), [("admin", True), ("co_admin", False)])
def test_member_form_phone_field_by_role(
    client_with_auth: object, role: str, editable: bool
) -> None:
    client = client_with_auth.mount(permissions=_ALL_PERMS, role=role)  # type: ignore[attr-defined]
    member = _member(phone="+34 600 111 222")
    with patch(
        "app.routes.web.settings_scheduling.membership_service.get_editable_member",
        AsyncMock(return_value=member),
    ):
        r = client.get(f"/settings/members/{member.membership_id}/edit", headers=auth_headers())

    assert r.status_code == 200
    assert ('name="phone"' in r.text) is editable
    assert ("member-phone-readonly" in r.text) is not editable
    assert "+34 600 111 222" in r.text


def test_admin_saves_phone(client_with_auth: object) -> None:
    client = client_with_auth.mount(permissions=_ALL_PERMS, role="admin")  # type: ignore[attr-defined]
    member = _member(phone="+34 600 111 222")
    update = AsyncMock(return_value=member)
    with patch(
        "app.routes.web.settings_scheduling.membership_service.update_tenant_member", update
    ):
        r = _post(
            client,
            client_with_auth,
            member.membership_id,
            {"perm_view": "true", "phone": "+34  600 111 222"},
        )

    assert r.status_code == 200  # type: ignore[attr-defined]
    payload = update.await_args.args[3]
    assert "phone" in payload.model_fields_set
    assert payload.phone == "+34 600 111 222"
    assert update.await_args.kwargs["actor_role"] == "admin"


def test_invalid_phone_is_rejected_before_saving(client_with_auth: object) -> None:
    client = client_with_auth.mount(permissions=_ALL_PERMS, role="admin")  # type: ignore[attr-defined]
    update = AsyncMock()
    with patch(
        "app.routes.web.settings_scheduling.membership_service.update_tenant_member", update
    ):
        r = _post(client, client_with_auth, uuid4(), {"phone": "600-111"})

    assert r.status_code in (400, 422)  # type: ignore[attr-defined]
    update.assert_not_awaited()


def test_co_admin_cannot_send_phone(client_with_auth: object) -> None:
    """Aunque un co_admin fuerce el campo en la petición, el servicio lo rechaza."""
    client = client_with_auth.mount(permissions=_ALL_PERMS, role="co_admin")  # type: ignore[attr-defined]
    r = _post(client, client_with_auth, uuid4(), {"perm_view": "true", "phone": "600111222"})

    assert r.status_code == 403  # type: ignore[attr-defined]
