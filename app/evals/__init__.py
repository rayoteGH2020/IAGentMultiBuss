"""Paquete de evals de calidad LLM para el módulo 1 (extracción de facturas).

Estructura:
  datasets/   — ficheros JSON con los casos de prueba y ground truth anotado.
  runners/    — scripts que ejecutan los casos contra el LLM y calculan métricas.
  results/    — JSONs de resultados por ejecución (excluidos de Git via .gitignore).
  conftest.py — fixtures de pytest compartidas por los runners.

Los evals son distintos de los tests de integración:
  - Los tests verifican comportamiento del código (mocks del LLM).
  - Los evals miden calidad de la extracción contra el LLM real.

Punto de entrada habitual:
    uv run python -m app.evals.runners.extraction <tenant_uuid>
CI lo ejecuta via .github/workflows/evals.yml en PRs que tocan app/llm/**,
app/schemas/invoice.py o app/evals/**.
"""
