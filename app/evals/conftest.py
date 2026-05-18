"""Fixtures comunes para evals LLM (datasets, paths, marker)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

EVALS_DIR = Path(__file__).parent
DATASETS_DIR = EVALS_DIR / "datasets"
RESULTS_DIR = EVALS_DIR / "results"
FIXTURES_DIR = EVALS_DIR.parent.parent / "tests" / "fixtures" / "invoices"


@pytest.fixture(scope="session")
def evals_results_dir() -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR


@pytest.fixture(scope="session")
def invoices_dataset() -> dict[str, object]:
    path = DATASETS_DIR / "invoices_v1.json"
    if not path.exists():
        pytest.skip(f"Dataset no encontrado: {path}")
    data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="session")
def invoices_fixtures_dir() -> Path:
    return FIXTURES_DIR
