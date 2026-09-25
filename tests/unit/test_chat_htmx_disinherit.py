"""Guards: navegación boost no debe vaciar fragments HTMX del chat."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_main_disinherits_and_sidebar_owns_boost() -> None:
    """Boost en sidebar; #main-content corta herencia por defensa en profundidad."""
    layout = (ROOT / "app/templates/layouts/dashboard.html").read_text(encoding="utf-8")
    sidebar = (ROOT / "app/templates/components/sidebar.html").read_text(encoding="utf-8")
    assert 'id="app-frame"' in layout
    assert 'hx-boost="true"' not in layout
    assert 'hx-boost="true"' in sidebar
    assert 'hx-select="#app-frame"' in sidebar
    assert 'id="main-content"' in layout
    assert 'hx-disinherit="*"' in layout


def test_chat_shell_disinherits_htmx_attrs() -> None:
    source = (ROOT / "app/templates/pages/chat/index.html").read_text(encoding="utf-8")
    assert 'id="chat-app"' in source
    assert "hx-disinherit" in source


def test_chat_create_response_template_has_panel_and_sidebar_oob() -> None:
    oob = (ROOT / "app/templates/components/chat_thread_panel_oob.html").read_text(encoding="utf-8")
    panel = (ROOT / "app/templates/components/chat_thread_panel.html").read_text(encoding="utf-8")
    assert "chat_thread_panel.html" in oob
    assert "chat_sidebar_oob.html" in oob
    assert 'id="chat-thread-panel"' in panel
    assert "chat_composer.html" in panel
