"""Tests del HX-Trigger de subida knowledge (cierre de modal solo si hay docs creados)."""

from __future__ import annotations

from app.routes.web.knowledge import _upload_result_message


def test_upload_result_ok_when_at_least_one_created() -> None:
    payload = _upload_result_message(
        created=1,
        errors=[{"filename": "bad.pdf", "error": "tipo no permitido"}],
    )
    assert payload["ok"] is True
    assert payload["created"] == 1


def test_upload_result_keeps_modal_open_without_files() -> None:
    payload = _upload_result_message(
        created=0,
        errors=[{"filename": "—", "error": "No se ha seleccionado ningún fichero."}],
    )
    assert payload["ok"] is False
    assert payload["message"] == "No se ha seleccionado ningún fichero."


def test_upload_result_keeps_modal_open_without_kind() -> None:
    payload = _upload_result_message(
        created=0,
        errors=[{"filename": "—", "error": "Debes seleccionar una categoría para el documento."}],
    )
    assert payload["ok"] is False
    assert "categoría" in str(payload["message"])


def test_upload_result_joins_multiple_file_errors() -> None:
    payload = _upload_result_message(
        created=0,
        errors=[
            {"filename": "a.zip", "error": "MIME no permitido"},
            {"filename": "b.exe", "error": "MIME no permitido"},
        ],
    )
    assert payload["ok"] is False
    assert "a.zip" in str(payload["message"])
    assert "b.exe" in str(payload["message"])
