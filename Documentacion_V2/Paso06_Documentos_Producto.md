# Paso06 - Documentos: calidad, UX y coste

Estado: **cerrado en codigo** (2026-09-23). No reabrir salvo regresion de producto.

Objetivo: hacer el modulo documental fiable para uso real.

## Dependencias

- Paso01 completado.
- Paso03/Paso04 recomendados si hay clientes reales.

## Alcance

- Verificacion de tipo documental antes de extraccion cara.
- Calidad de extraccion para documentos vacios/no validos.
- UX de documentos en processing.
- Multi-IVA visible y usable.
- Prompts de contratos y seguros.

## Tareas

### 1. Verificacion de tipo

- [x] Revisar `document_classification.py`.
- [x] No confiar ciegamente en tipo elegido por usuario para factura/ticket.
- [x] Usar heuristica barata antes de LLM.
- [x] LLM classify solo si hay duda o conflicto.
- [x] Si mismatch con confianza alta, pedir confirmacion HTMX.
- [x] No encolar extraccion cara hasta confirmar.

### 2. Documentos invalidos o vacios

- [x] Si no hay informacion util, marcar fallo claro.
- [x] No guardar "basura" como documento correcto.
- [x] Mensaje visible para usuario.
- [x] Error interno sin contenido sensible en logs externos.

### 3. Multi-IVA

- [x] Confirmar que `vat_breakdown` se rellena.
- [x] En resumen, si hay varios tramos, mostrar "Multiple" o desglose.
- [x] En detalle, mostrar base/porcentaje/importe por tramo.
- [x] Evals con facturas reales multi-IVA.

### 4. UX processing

- [x] Simplificar fila mientras procesa.
- [x] Mostrar estado, spinner e identificador minimo.
- [x] No mostrar campos vacios como si fueran datos finales.

### 5. Contratos y seguros

- [x] Revisar prompts.
- [x] Revisar schemas.
- [x] Evals minimas o fixtures.
- [x] No exponer datos sensibles innecesarios al chat/tools.

## Tests

- [x] Unit de heuristica factura/ticket.
- [x] Unit de mismatch sin encolar.
- [x] Integration upload mismatch -> confirmacion.
- [x] Integration confirmar sugerencia -> job correcto.
- [x] Integration mantener tipo usuario -> job elegido.
- [x] Unit multi-IVA display.
- [x] Evals extraccion multi-IVA.

## Comandos

```powershell
infisical run -- uv run pytest tests/unit/test_document_classification.py tests/unit/test_document_upload_routing.py tests/unit/test_document_type_confirm.py tests/unit/test_extraction_quality.py tests/unit/test_contract_insurance_prompts.py tests/unit/test_invoice_schema.py tests/unit/test_invoice_service.py -q
infisical run -- uv run pytest tests/integration/test_invoice_upload.py tests/integration/test_documents_web.py -q
infisical run -- uv run python -m app.evals.runners.extraction
infisical run -- uv run ruff check app tests
infisical run -- uv run mypy app
```

## Criterios de aceptacion

- [x] Un documento mal tipificado no genera extraccion cara sin confirmacion.
- [x] Un documento vacio/no valido no queda como correcto.
- [x] Multi-IVA es visible de forma clara.
- [x] Contratos/seguros no dependen de prompts pobres sin tests.

## Notas de implementacion

- Confirmacion de tipo: `document_type_confirm_service` + `POST /documents/{kind}/{id}/confirm-type`.
- Quality gate: `extraction_quality.py` en `apply_extraction_result` (factura/ticket/contrato/seguro).
- Multi-IVA en listado: `PanelDocumentRow.iva_percent_label` → "Múltiple" si `vat_tranche_count > 1`.
- Dataset eval multi-IVA: `app/evals/datasets/invoices_v2.json` (fixtures reales pendientes de anotar ground truth).
