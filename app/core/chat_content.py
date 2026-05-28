"""Utilidades de renderizado de contenido de mensajes de chat."""

from __future__ import annotations

import re

from markupsafe import Markup, escape

_CITATION_REF_PATTERN = re.compile(r"\s*\[\d+\]")


def chat_content_plain(text: str | None) -> Markup:
    """Texto del asistente escapado, sin referencias inline ``[N]`` ni HTML."""

    if not text:
        return Markup("")

    without_refs = _CITATION_REF_PATTERN.sub("", text)

    return escape(without_refs)
