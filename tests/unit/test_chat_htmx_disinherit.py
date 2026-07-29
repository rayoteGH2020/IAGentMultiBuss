"""Guards: navegación boost no debe vaciar fragments HTMX del chat."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_main_disinherits_app_frame_htmx_attrs() -> None:
    """#app-frame usa hx-select; #main-content debe cortar la herencia."""
    source = (ROOT / "app/templates/layouts/dashboard.html").read_text(encoding="utf-8")
    assert 'id="app-frame"' in source
    assert 'hx-select="#app-frame"' in source
    assert 'id="main-content"' in source
    assert 'hx-disinherit="*"' in source


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
