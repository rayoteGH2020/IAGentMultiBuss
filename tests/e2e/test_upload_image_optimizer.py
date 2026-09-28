"""Helper de optimización de fotos en el cliente (app/static/js/alpine-components.js).

Necesita un navegador real: `createImageBitmap` y `canvas.toBlob` no existen en
Python. No necesita servidor ni sesión, a diferencia del resto de tests e2e, así
que se salta solo si Chromium no está instalado.

    uv run playwright install chromium
    uv run pytest tests/e2e/test_upload_image_optimizer.py -q
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

pytestmark = pytest.mark.e2e

COMPONENTS_JS = (
    Path(__file__).resolve().parents[2] / "app" / "static" / "js" / "alpine-components.js"
)

# Construye un JPEG parecido a una foto (degradados suaves con algo de grano,
# no ruido puro: el ruido comprime al revés que una foto real y falsearía la
# comparación de tamaños) y devuelve el resultado del helper ya medido.
_RUN_OPTIMIZER = """
async ({ width, height }) => {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  const gradient = ctx.createLinearGradient(0, 0, width, height);
  gradient.addColorStop(0, "#1b2a4a");
  gradient.addColorStop(0.5, "#c8a06a");
  gradient.addColorStop(1, "#f2f0e6");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, width, height);
  for (let i = 0; i < 400; i += 1) {
    ctx.fillStyle = `rgba(0,0,0,${(i % 7) / 40})`;
    ctx.fillRect((i * 37) % width, (i * 53) % height, 24, 9);
  }

  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.95));
  const original = new File([blob], "foto.jpeg", { type: "image/jpeg" });
  const result = await window.optimizeImageFile(original);
  const bitmap = await createImageBitmap(result);

  return {
    name: result.name,
    type: result.type,
    size: result.size,
    originalSize: original.size,
    width: bitmap.width,
    height: bitmap.height,
  };
}
"""

_PASSTHROUGH = """
async ({ name, type, bytes }) => {
  const file = new File([new Uint8Array(bytes)], name, { type });
  const result = await window.optimizeImageFile(file);
  return { same: result === file, name: result.name, type: result.type };
}
"""

# Registra los componentes con un Alpine mínimo y ejercita documentUploadForm
# igual que lo haría el usuario: coloca `count` fotos en las zonas del modal.
_PLACE_FILES_IN_SLOTS = """
async ({ count }) => {
  const registry = {};
  window.Alpine = { data: (name, factory) => { registry[name] = factory; } };
  document.dispatchEvent(new CustomEvent("alpine:init"));

  const canvas = document.createElement("canvas");
  canvas.width = 2000;
  canvas.height = 1400;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#8899aa";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.95));

  const files = [];
  for (let i = 0; i < count; i += 1) {
    files.push(new File([blob], `ticket-${i}.jpeg`, { type: "image/jpeg" }));
  }

  const component = registry.documentUploadForm();
  const pending = component.placeFiles(files);
  const optimizingWhileWorking = component.optimizing;
  await pending;

  return {
    optimizingWhileWorking,
    optimizingAfter: component.optimizing,
    filledCount: component.filledCount,
    formError: component.formError,
    canSubmit: component.canSubmit,
    names: component.filledSlots.map((slot) => slot.file.name),
    types: component.filledSlots.map((slot) => slot.file.type),
    shrank: component.filledSlots.every((slot) => slot.file.size < blob.size),
  };
}
"""


_ACCEPT_FILES_IN_KNOWLEDGE = """
async () => {
  const registry = {};
  window.Alpine = { data: (name, factory) => { registry[name] = factory; } };
  document.dispatchEvent(new CustomEvent("alpine:init"));

  const canvas = document.createElement("canvas");
  canvas.width = 2000;
  canvas.height = 1400;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#8899aa";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.95));

  const component = registry.knowledgeUploadForm();
  const pending = component.placeFiles([new File([blob], "apuntes.jpeg", { type: "image/jpeg" })]);
  const optimizingWhileWorking = component.optimizing;
  await pending;

  const files = component.filledSlots.map((slot) => slot.file);
  return {
    optimizingWhileWorking,
    optimizingAfter: component.optimizing,
    names: files.map((f) => f.name),
    types: files.map((f) => f.type),
    shrank: files.every((f) => f.size < blob.size),
  };
}
"""


async def _with_page(evaluate: str, arg: dict[str, Any]) -> Any:
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except PlaywrightError as exc:  # navegador no instalado en el entorno
            pytest.skip(f"Chromium no disponible: {exc}")
        try:
            page = await browser.new_page()
            await page.goto("about:blank")
            await page.add_script_tag(path=str(COMPONENTS_JS))
            return await page.evaluate(evaluate, arg)
        finally:
            await browser.close()


async def test_large_photo_is_downscaled_and_converted_to_jpeg() -> None:
    result = await _with_page(_RUN_OPTIMIZER, {"width": 2400, "height": 1600})

    assert result["type"] == "image/jpeg"
    assert result["name"] == "foto.jpg"
    # 1800 px de lado largo: margen sobre los 1280 px a los que reduce el servidor.
    assert max(result["width"], result["height"]) == 1800
    assert result["size"] < result["originalSize"]


async def test_small_photo_is_not_upscaled() -> None:
    result = await _with_page(_RUN_OPTIMIZER, {"width": 800, "height": 400})

    assert (result["width"], result["height"]) == (800, 400)


async def test_non_image_files_are_not_touched() -> None:
    result = await _with_page(
        _PASSTHROUGH,
        {"name": "factura.pdf", "type": "application/pdf", "bytes": list(b"%PDF-1.4 x")},
    )

    assert result["same"] is True
    assert result["name"] == "factura.pdf"


async def test_upload_modal_stores_the_optimized_photos_in_its_slots() -> None:
    result = await _with_page(_PLACE_FILES_IN_SLOTS, {"count": 3})

    assert result["filledCount"] == 3
    assert result["names"] == ["ticket-0.jpg", "ticket-1.jpg", "ticket-2.jpg"]
    assert result["types"] == ["image/jpeg"] * 3
    assert result["shrank"] is True
    assert result["formError"] == ""
    # El botón de enviar queda bloqueado mientras se optimiza y luego solo
    # espera a que el usuario elija el tipo de cada documento.
    assert result["optimizingWhileWorking"] is True
    assert result["optimizingAfter"] is False
    assert result["canSubmit"] is False


async def test_knowledge_modal_stores_the_optimized_photos() -> None:
    result = await _with_page(_ACCEPT_FILES_IN_KNOWLEDGE, {})

    assert result["names"] == ["apuntes.jpg"]
    assert result["types"] == ["image/jpeg"]
    assert result["shrank"] is True
    assert result["optimizingWhileWorking"] is True
    assert result["optimizingAfter"] is False


async def test_upload_modal_rejects_more_photos_than_free_slots() -> None:
    result = await _with_page(_PLACE_FILES_IN_SLOTS, {"count": 11})

    assert result["filledCount"] == 0
    assert "Solo quedan 10 zonas libres" in result["formError"]


async def test_undecodable_image_falls_back_to_the_original() -> None:
    """Si el navegador no sabe abrir el formato, sube el original y decide el servidor."""
    result = await _with_page(
        _PASSTHROUGH,
        {"name": "foto.heic", "type": "image/heic", "bytes": list(b"no soy una imagen")},
    )

    assert result["same"] is True
    assert result["name"] == "foto.heic"
