"""Sidebar fijo en escritorio: no se estira con listados largos del main."""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SIDEBAR = _ROOT / "app" / "templates" / "components" / "sidebar.html"
_DASHBOARD = _ROOT / "app" / "templates" / "layouts" / "dashboard.html"

_STICKY_CLASSES = ("lg:sticky", "lg:top-0", "lg:h-screen", "lg:self-start", "lg:overflow-y-auto")


def _aside_classes() -> list[str]:
    html = _SIDEBAR.read_text(encoding="utf-8")
    match = re.search(r'<aside\s+class="([^"]+)"', html)
    assert match, "sidebar.html debe tener <aside class=...>"
    return match.group(1).split()


def test_sidebar_is_sticky_full_height_on_desktop() -> None:
    classes = _aside_classes()
    for cls in _STICKY_CLASSES:
        assert cls in classes, f"falta {cls} en el <aside> del sidebar"


def test_sidebar_keeps_mobile_hidden_by_default() -> None:
    classes = _aside_classes()
    assert "hidden" in classes
    assert "lg:flex" in classes


def test_page_scroll_stays_on_document_not_fixed_frame() -> None:
    # El sticky del sidebar depende de que el scroll sea el del documento:
    # si #app-frame pasa a h-screen/overflow, revisar este diseño.
    html = _DASHBOARD.read_text(encoding="utf-8")
    frame = re.search(r'id="app-frame"\s+class="([^"]+)"', html)
    assert frame
    frame_classes = frame.group(1).split()
    assert "min-h-screen" in frame_classes
    assert "h-screen" not in frame_classes
    assert "overflow-hidden" not in frame_classes
