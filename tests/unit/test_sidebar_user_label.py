"""Cabecera del sidebar: logo, organización y usuario conectado (nombre y email)."""

from __future__ import annotations

import re
from types import SimpleNamespace

from app.core.templating import templates


def _render(user: object) -> str:
    request = SimpleNamespace(
        url=SimpleNamespace(path="/chat"),
        state=SimpleNamespace(is_superadmin=False),
    )
    return templates.env.get_template("components/sidebar.html").render(
        request=request,
        tenant=SimpleNamespace(name="Peluqueria The Moon"),
        user=user,
        membership=SimpleNamespace(role="member"),
        entitlements=None,
    )


def _header(html: str) -> str:
    """Bloque de cabecera (antes del <nav>)."""
    return html.split("<nav", 1)[0]


def test_sidebar_shows_org_name_and_email_on_separate_lines() -> None:
    header = _header(_render(SimpleNamespace(name="rubencito", email="ruben.cito@gmail.com")))
    lines = re.findall(r'<span class="max-w-full truncate[^"]*"[^>]*>([^<]+)</span>', header)
    assert lines == ["Peluqueria The Moon", "rubencito", "ruben.cito@gmail.com"]
    assert " - ruben.cito@gmail.com" not in header
    # Mismo contenedor alineado a la izquierda para las tres líneas.
    assert 'class="min-w-0 flex flex-col items-start leading-tight"' in header


def test_sidebar_without_name_shows_only_email() -> None:
    header = _header(_render(SimpleNamespace(name=None, email="ana@example.com")))
    lines = re.findall(r'<span class="max-w-full truncate[^"]*"[^>]*>([^<]+)</span>', header)
    assert lines == ["Peluqueria The Moon", "ana@example.com"]


def test_sidebar_header_logo_and_gap_are_8_percent_bigger() -> None:
    header = _header(_render(SimpleNamespace(name="x", email="x@example.com")))
    assert '<div class="h-[69px] flex' in header  # 64 px x 1,08
    assert 'class="h-[43px] w-[43px] object-contain"' in header
    assert 'width="43"' in header and 'height="43"' in header
    assert "gap-[13px]" in header


def test_sidebar_escapes_user_values() -> None:
    header = _header(_render(SimpleNamespace(name="<b>x</b>", email="a@example.com")))
    assert "<b>x</b>" not in header
    assert "&lt;b&gt;x&lt;/b&gt;" in header
