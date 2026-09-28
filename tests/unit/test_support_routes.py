"""/settings/support: solo admin, pestaña en Ajustes, formulario y adjunto."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest
from app.core.errors import ForbiddenError, RateLimitError, ValidationError
from app.core.permissions import is_org_admin_role
from app.core.support_uploads import (
    ERR_TOO_LARGE,
    SUPPORT_ATTACHMENT_MAX_BYTES,
    SupportAttachmentError,
)
from app.core.templating import templates
from app.deps import RequireOrgAdmin
from app.routes.web import settings as settings_routes
from app.schemas.support import SUPPORT_KIND_LABELS, SUPPORT_SEVERITY_LABELS
from fastapi import UploadFile

_LAYOUT = Path(__file__).resolve().parents[2] / "app/templates/pages/settings/_layout.html"


@pytest.mark.parametrize(
    ("method", "endpoint"), [("GET", "settings_support"), ("POST", "settings_support_send")]
)
def test_support_routes_registered(method: str, endpoint: str) -> None:
    from app.main import create_app
    from starlette.routing import Match

    scope = {"type": "http", "method": method, "path": "/settings/support"}
    for route in create_app().router.routes:
        match, _ = route.matches(scope)
        if match is Match.FULL:
            assert getattr(route, "name", None) == endpoint
            return
    pytest.fail(f"No route matches {method} /settings/support")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "allowed"), [("admin", True), ("co_admin", False), ("member", False)]
)
async def test_require_org_admin_only_admin(role: str, allowed: bool) -> None:
    dep = get_args(RequireOrgAdmin)[1].dependency
    membership = SimpleNamespace(role=role)
    if allowed:
        assert await dep(membership=membership) is membership
    else:
        with pytest.raises(ForbiddenError):
            await dep(membership=membership)
    assert is_org_admin_role(role) is allowed


def test_support_tab_only_for_org_admin() -> None:
    layout = _LAYOUT.read_text(encoding="utf-8")
    assert "{% if membership and is_org_admin_role(membership.role) %}" in layout
    assert '("/settings/support", "Soporte técnico")' in layout


def _form_html(**ctx: object) -> str:
    base: dict[str, object] = {
        "kinds": SUPPORT_KIND_LABELS,
        "severities": SUPPORT_SEVERITY_LABELS,
        "title_max": 100,
        "message_max": 2000,
        "attachment_accept": ".docx,.txt,.jpg,.jpeg",
        "values": {},
        "error": None,
        "sent": False,
    }
    base.update(ctx)
    return templates.env.get_template("components/support/support_form.html").render(**base)


def test_form_fields_required_and_marked() -> None:
    html = _form_html()
    assert 'hx-post="/settings/support"' in html
    assert 'hx-encoding="multipart/form-data"' in html
    assert 'hx-target="#support-form-panel"' in html
    assert 'name="title" required maxlength="100"' in html
    assert 'name="message" required maxlength="2000"' in html
    assert '<select name="kind" required' in html
    assert '<select name="severity" required' in html
    assert html.count('<span class="text-red-600" aria-hidden="true">*</span>') == 4
    assert 'type="file" name="attachment" accept=".docx,.txt,.jpg,.jpeg"' in html
    assert "(opcional)" in html
    for label in ("Error", "Duda", "Sugerencia", "Ayuda", "Baja", "Media", "Alta", "Crítica"):
        assert f">{label}</option>" in html
    assert ">Enviar</button>" in html


def test_form_keeps_values_on_error_and_escapes() -> None:
    html = _form_html(
        values={"title": '"><script>', "message": "<b>x</b>", "kind": "help", "severity": "high"},
        error="Revisa estos campos: Título.",
    )
    assert 'role="alert"' in html and "Revisa estos campos" in html
    assert "<script>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html
    assert '<option value="help" selected>' in html
    assert '<option value="high" selected>' in html


def test_form_shows_remaining_chars_counters() -> None:
    """Contador por campo: se recalcula en cada input (teclado o pegado) y al pintar."""
    html = _form_html(values={"title": "Hola", "message": "abc"})
    assert '@input="left = 100 - $el.value.length"' in html
    assert '@input="left = 2000 - $el.value.length"' in html
    assert 'x-init="left = 100 - $refs.field.value.length"' in html
    assert 'x-init="left = 2000 - $refs.field.value.length"' in html
    # Valor inicial correcto sin JS (formulario devuelto con error).
    assert 'Quedan <span x-text="left">96</span> de 100 caracteres.' in html
    assert 'Quedan <span x-text="left">1997</span> de 2000 caracteres.' in html
    assert "left <= 10 ? 'text-amber-600' : 'text-slate-400'" in html
    assert "left <= 200 ? 'text-amber-600' : 'text-slate-400'" in html


def test_form_success_notice() -> None:
    assert "Mensaje enviado al equipo de soporte." in _form_html(sent=True)


def _upload(name: str, data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


@pytest.mark.asyncio
async def test_read_attachment_none_when_input_empty() -> None:
    assert await settings_routes._read_attachment(None) is None
    assert await settings_routes._read_attachment(_upload("", b"")) is None


@pytest.mark.asyncio
async def test_read_attachment_rejects_oversize_without_reading_all() -> None:
    with pytest.raises(SupportAttachmentError) as exc:
        await settings_routes._read_attachment(
            _upload("a.txt", b"a" * (SUPPORT_ATTACHMENT_MAX_BYTES + 10))
        )
    assert exc.value.code == ERR_TOO_LARGE


@pytest.mark.asyncio
async def test_read_attachment_valid_txt() -> None:
    attachment = await settings_routes._read_attachment(_upload("log.txt", b"hola"))
    assert attachment is not None and attachment.content_type == "text/plain"


def test_error_messages_mapping() -> None:
    assert "máximo de mensajes" in settings_routes._support_error(RateLimitError("x"))
    missing = ValidationError("x", details={"code": "email_sadm_missing"})
    assert "EMAIL_SADM" in settings_routes._support_error(missing)
    assert "Inténtalo de nuevo" in settings_routes._support_error(ValidationError("x"))
