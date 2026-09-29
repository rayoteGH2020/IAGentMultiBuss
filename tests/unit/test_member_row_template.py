"""/settings/members: fila con rol y modal de solicitud de baja al SADM."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from app.core.datetime_display import local_datetime
from app.core.templating import templates
from app.schemas.membership import MemberRemovalForm, MembershipPermissions, TenantMemberRead


def _member(name: str | None = "Ana O'Neil", role: str = "member") -> TenantMemberRead:
    return TenantMemberRead(
        membership_id=uuid4(),
        user_id=uuid4(),
        email="ana@example.com",
        name=name,
        role=role,
        permissions=MembershipPermissions(),
        clerk_user_id="user_1",
    )


def _render(template: str, **ctx: object) -> str:
    return templates.env.get_template(f"components/scheduling/{template}").render(**ctx)


def test_row_shows_role_and_opens_removal_modal_when_allowed() -> None:
    member = _member(role="co_admin")
    html = _render("member_row.html", member=member, removable_ids={member.membership_id})

    assert "Co-administrador" in html
    assert f'hx-get="/settings/members/{member.membership_id}/removal-request"' in html
    assert 'hx-target="#member-modal-panel"' in html
    assert "hx-delete" not in html


def test_row_actions_are_icons_not_text() -> None:
    member = _member()
    html = _render("member_row.html", member=member, removable_ids={member.membership_id})
    pencil = templates.env.get_template("components/icons/pencil.html").render()
    trash = templates.env.get_template("components/icons/trash.html").render()

    assert pencil.strip() in html
    assert trash.strip() in html
    assert 'title="Editar"' in html and 'title="Solicitar baja"' in html
    assert 'aria-label="Solicitar baja de Ana O&#39;Neil"' in html
    assert ">Editar<" not in html and ">Baja<" not in html


def test_row_keeps_both_action_columns_when_removal_hidden() -> None:
    """Sin botón de baja la celda sigue existiendo: los iconos no se desplazan."""
    member = _member(role="admin")
    allowed = _render("member_row.html", member=_member(), removable_ids=None)
    hidden = _render("member_row.html", member=member, removable_ids=set())

    assert hidden.count('<td class="w-12 text-center">') == 2
    assert allowed.count("<td") == hidden.count("<td")
    assert "removal-request" not in hidden


def test_row_pending_removal_replaces_action_icons_with_notice() -> None:
    # 23:30 UTC ya es día 29 en Europe/Madrid: la fecha se muestra en hora local.
    requested_at = datetime(2026, 9, 28, 23, 30, tzinfo=UTC)
    member = _member().model_copy(
        update={"removal_requested_at": requested_at, "removal_effective_date": date(2026, 10, 9)}
    )
    # removable_ids incluye la fila a propósito: el estado pendiente manda.
    html = _render("member_row.html", member=member, removable_ids={member.membership_id})

    requested = local_datetime(requested_at, "%d/%m/%Y")
    assert f"Baja solicitada el {requested}, efectiva desde el 09/10/2026" in html
    assert '<td colspan="2" class="text-center' in html
    # Toda la fila en rojo; ninguna celda impone su propio color de texto.
    assert f'<tr id="member-{member.membership_id}" class="bg-red-50 text-red-700">' in html
    assert "text-slate-600" not in html
    assert html.count("<td") == 5  # 4 de datos + 1 que abarca editar y baja
    assert "components/icons" not in html and "<svg" not in html
    assert "/edit" not in html and "removal-request" not in html
    assert html.count("<tr") == 1


def test_row_without_pending_removal_keeps_both_icon_cells() -> None:
    html = _render("member_row.html", member=_member(), removable_ids=set())
    assert "colspan" not in html
    assert "bg-red-50" not in html and "text-red-700" not in html
    assert html.count('<td class="w-12 text-center">') == 2
    assert "/edit" in html


def test_row_success_notice_shown_under_email() -> None:
    member = _member().model_copy(update={"removal_effective_date": date(2026, 10, 5)})
    html = _render(
        "member_row.html",
        member=member,
        removable_ids=set(),
        removal_notice="Baja solicitada al superadmin (efectiva 05/10/2026).",
        removal_notice_ok=True,
    )
    assert "Baja solicitada al superadmin" in html
    assert "text-emerald-700" in html


def test_row_error_notice_keeps_button_for_retry() -> None:
    member = _member()
    html = _render(
        "member_row.html",
        member=member,
        removable_ids={member.membership_id},
        removal_notice="No se pudo enviar la solicitud.",
        removal_notice_ok=False,
    )
    assert "text-red-600" in html
    assert "removal-request" in html


def _form(member: TenantMemberRead) -> MemberRemovalForm:
    return MemberRemovalForm(
        member=member,
        actor_name="Jefa",
        actor_email="jefa@example.com",
        actor_role="co_admin",
        min_date=date(2026, 9, 28),
        max_date=date(2027, 9, 28),
    )


def test_removal_form_confirmation_actor_role_and_date() -> None:
    member = _member()
    html = _render("member_removal_form.html", form=_form(member))

    assert (
        'Se va a comunicar al admin la baja del usuario "Ana O&#39;Neil", ¿Deseas continuar?'
    ) in html
    assert "Jefa · Co-administrador" in html
    assert "Fecha baja efectiva" in html
    assert 'name="effective_date"' in html
    assert 'min="2026-09-28"' in html and 'max="2027-09-28"' in html
    assert f'hx-post="/settings/members/{member.membership_id}/removal-request"' in html
    assert f'hx-target="#member-{member.membership_id}"' in html
    assert ">Aceptar<" in html and ">Cancelar<" in html


def test_removal_form_falls_back_to_email() -> None:
    html = _render("member_removal_form.html", form=_form(_member(name=None)))
    assert 'baja del usuario "ana@example.com"' in html


def test_member_form_phone_editable_only_for_admin() -> None:
    member = _member().model_copy(update={"phone": "+34 600 111 222"})

    admin_html = _render("member_form.html", member=member, can_edit_phone=True)
    assert 'name="phone"' in admin_html
    assert 'value="+34 600 111 222"' in admin_html
    assert "member-phone-readonly" not in admin_html

    co_admin_html = _render("member_form.html", member=member, can_edit_phone=False)
    assert 'name="phone"' not in co_admin_html
    assert "member-phone-readonly" in co_admin_html
    assert "+34 600 111 222" in co_admin_html
