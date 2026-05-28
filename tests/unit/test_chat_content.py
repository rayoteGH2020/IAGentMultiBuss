"""Renderizado de contenido de mensajes de chat."""

from __future__ import annotations

from app.core.chat_content import chat_content_plain
from markupsafe import Markup


def test_chat_content_plain_empty() -> None:
    assert chat_content_plain(None) == Markup("")

    assert chat_content_plain("") == Markup("")


def test_chat_content_plain_strips_citation_refs() -> None:
    html = str(chat_content_plain("Horario [1] y política [2]."))

    assert "Horario" in html

    assert "política" in html

    assert "[1]" not in html

    assert "[2]" not in html

    assert "data-cite-open" not in html


def test_chat_content_escapes_html_injection() -> None:
    html = str(chat_content_plain("<b>mal</b> [1]"))

    assert "&lt;b&gt;mal&lt;/b&gt;" in html

    assert "<b>mal</b>" not in html

    assert "[1]" not in html
