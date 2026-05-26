"""Tests de utilidades de subida."""

from app.core.uploads import original_upload_filename


def test_original_upload_filename_strips_path() -> None:
    assert original_upload_filename(r"C:\Users\doc\factura.pdf") == "factura.pdf"
    assert original_upload_filename("folder/ticket.jpg") == "ticket.jpg"


def test_original_upload_filename_empty_fallback() -> None:
    assert original_upload_filename(None) == "sin-nombre"
    assert original_upload_filename("   ") == "sin-nombre"
