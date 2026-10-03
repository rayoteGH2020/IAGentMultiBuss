"""Guards HTMX/Alpine del panel de documentos (poll sin historial boost)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
STATIC_JS = ROOT / "app" / "static" / "js"


def test_document_row_includes_type_icon_without_extra_header() -> None:
    row = (TEMPLATES / "components" / "document_row.html").read_text(encoding="utf-8")
    head = (TEMPLATES / "components" / "invoices_table_head.html").read_text(encoding="utf-8")
    icon = (TEMPLATES / "components" / "document_type_icon.html").read_text(encoding="utf-8")
    assert 'include "components/document_type_icon.html"' in row
    assert row.count('include "components/document_type_icon.html"') >= 2
    assert 'sort_th("Tipo"' in head
    assert "Icono" not in head
    assert "factura" in icon
    assert "ticket" in icon
    assert "contrato" in icon
    assert "seguro" in icon


def test_document_status_poll_not_registered_in_alpine() -> None:
    js = (STATIC_JS / "alpine-components.js").read_text(encoding="utf-8")
    assert "documentStatusPoll" not in js
    assert "registerDocumentStatusPoll" not in js
    assert 'hx-target") === "this"' in js or 'hxTarget === "this"' in js


def test_upload_form_does_not_push_url() -> None:
    html = (TEMPLATES / "components" / "upload_modal.html").read_text(encoding="utf-8")
    assert 'hx-post="/documents/upload"' in html
    assert 'hx-push-url="false"' in html
    assert 'hx-history="false"' in html


def test_dashboard_boost_lives_on_sidebar_not_app_frame() -> None:
    layout = (TEMPLATES / "layouts" / "dashboard.html").read_text(encoding="utf-8")
    sidebar = (TEMPLATES / "components" / "sidebar.html").read_text(encoding="utf-8")
    assert 'id="app-frame"' in layout
    assert 'hx-boost="true"' not in layout
    assert 'hx-boost="true"' in sidebar
    assert 'hx-target="#app-frame"' in sidebar
    assert 'hx-select="#app-frame"' in sidebar
    assert 'hx-push-url="true"' in sidebar


def test_jobs_status_sets_no_store_and_no_push() -> None:
    source = (ROOT / "app" / "routes" / "web" / "jobs.py").read_text(encoding="utf-8")
    assert 'HX-Push-Url"] = "false"' in source
    assert 'Cache-Control"] = "no-store"' in source


def test_jobs_terminal_status_retargets_documents_panel() -> None:
    source = (ROOT / "app" / "routes" / "web" / "jobs.py").read_text(encoding="utf-8")
    assert 'HX-Retarget"] = "#invoices-table-container"' in source
    assert 'HX-Reswap"] = "outerHTML"' in source
    assert "is_document_status_busy" in source
    assert "build_invoices_panel_ctx" in source
    assert "just_uploaded_ids=[]" in source


def test_is_document_status_busy() -> None:
    from app.services.document_panel_service import is_document_status_busy

    assert is_document_status_busy("pending")
    assert is_document_status_busy("processing")
    assert not is_document_status_busy("ready")
    assert not is_document_status_busy("failed")
