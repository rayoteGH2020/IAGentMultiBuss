"""Los datasets de evals deben ser JSON válido (sin comas finales)."""

from __future__ import annotations

import json
from pathlib import Path

DATASETS_DIR = Path(__file__).resolve().parents[2] / "app" / "evals" / "datasets"


def test_eval_datasets_are_valid_json() -> None:
    for path in sorted(DATASETS_DIR.glob("*.json")):
        json.loads(path.read_text(encoding="utf-8"))
