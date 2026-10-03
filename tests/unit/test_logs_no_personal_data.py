"""Guardia estática: ningún ``logger.*`` de app/ registra datos personales (P2c-2).

Norma completa en ``app/core/log_redaction.py``. Si un campo nuevo es legítimo,
renómbralo para que diga lo que lleva (``*_ref`` seudonimizado, ``error_type``...).
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"
_LEVELS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical"})

# Campos con datos personales o contenido extraído de documentos.
_FORBIDDEN_FIELDS = frozenset(
    {
        "aseguradora",
        "chat_id",
        "client_name",
        "comercio",
        "customer",
        "customer_identifier",
        "email",
        "filename",
        "google_email",
        "parte_contraria",
        "phone",
        "proveedor",
        "source_filename",
        "subject",
        "technical_error",
        "to",
        "total",
    }
)
# Texto de excepciones de terceros: puede llevar contenido del cliente.
_FORBIDDEN_VALUES = ("str(exc)", "str(e)", "str(err)", "str(error)", "repr(exc)")


def _logger_calls(tree: ast.AST) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _LEVELS:
            continue
        base = ast.unparse(node.func.value).lower()
        if base.endswith("log") or base.endswith("logger"):
            calls.append(node)
    return calls


def _violations() -> list[str]:
    found: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if "evals" in path.relative_to(APP_DIR).parts:
            continue
        source = path.read_text(encoding="utf-8")
        for call in _logger_calls(ast.parse(source)):
            where = f"{path.relative_to(APP_DIR.parent).as_posix()}:{call.lineno}"
            for kw in call.keywords:
                value = ast.get_source_segment(source, kw.value) or ""
                if kw.arg in _FORBIDDEN_FIELDS:
                    found.append(f"{where} campo {kw.arg}=")
                if any(token in value.replace(" ", "") for token in _FORBIDDEN_VALUES):
                    found.append(f"{where} {kw.arg}={value}")
    return found


def test_logger_calls_have_no_personal_data() -> None:
    assert _violations() == []


def test_guard_detects_known_patterns() -> None:
    sample = (
        "logger.info('x', customer_identifier=c)\n"
        "log.warning('y', error=str(exc))\n"
        "logger.info('z', customer_ref=pseudonymize(c), error_type=type(exc).__name__)\n"
    )
    calls = _logger_calls(ast.parse(sample))
    flagged = [
        kw.arg
        for call in calls
        for kw in call.keywords
        if kw.arg in _FORBIDDEN_FIELDS
        or any(t in (ast.get_source_segment(sample, kw.value) or "") for t in _FORBIDDEN_VALUES)
    ]

    assert len(calls) == 3
    assert flagged == ["customer_identifier", "error"]
