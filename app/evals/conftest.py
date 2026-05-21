"""Fixtures comunes para evals LLM (datasets, paths, marker).

Este conftest.py está separado del conftest principal de tests/ para aislar
las fixtures de evals de las de tests de integración. Los evals tienen un
ciclo de vida distinto (scope="session", dependencias de ficheros externos)
y no deben contaminar la sesión de tests normales.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Rutas relativas al fichero: funcionan independientemente del directorio de
# trabajo desde el que se lanza pytest o el runner.
EVALS_DIR = Path(__file__).parent
DATASETS_DIR = EVALS_DIR / "datasets"
RESULTS_DIR = EVALS_DIR / "results"
# Los fixtures PDF se comparten con tests/integration/ para no duplicar
# archivos binarios en el repositorio.
FIXTURES_DIR = EVALS_DIR.parent.parent / "tests" / "fixtures" / "invoices"


@pytest.fixture(scope="session")
def evals_results_dir() -> Path:
    # scope="session": el directorio se crea una sola vez por sesión de pytest,
    # no una vez por test. Los evals pueden tardar varios minutos; el scope
    # session evita setup redundante y comparte el directorio entre runners.
    # exist_ok=True: no falla si el directorio ya existe de una ejecución previa.
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR


@pytest.fixture(scope="session")
def invoices_dataset() -> dict[str, object]:
    # scope="session": el JSON se parsea una sola vez y se comparte entre
    # todos los tests de la sesión. El dataset puede tener decenas de casos;
    # releer y parsear el JSON en cada test sería innecesario.
    path = DATASETS_DIR / "invoices_v1.json"
    if not path.exists():
        # pytest.skip en lugar de FileNotFoundError: permite ejecutar el resto
        # de la suite de tests aunque no exista el dataset de evals. Útil en
        # entornos donde solo se quieren correr los tests de integración.
        pytest.skip(f"Dataset no encontrado: {path}")
    data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="session")
def invoices_fixtures_dir() -> Path:
    # Devuelve la ruta al directorio de PDFs de muestra. Los runners leen los
    # bytes de los PDF directamente en lugar de usar el fixture de pytest porque
    # el runner principal (extraction.py) puede ejecutarse sin pytest.
    return FIXTURES_DIR
