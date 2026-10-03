"""Seudónimos de la metadata de auditoría y su configuración (Backlog P2c-7)."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.config import Settings
from app.core import audit_pseudonym
from pydantic import SecretStr, ValidationError

_BASE = {
    "app_secret_key": "x" * 32,
    "database_url": "postgresql+asyncpg://u:p@localhost/db",  # pragma: allowlist secret
    "redis_url": "redis://localhost:6379/0",
    "clerk_jwks_url": "",
    "webhook_allow_unsigned": False,
    "langfuse_capture_content": False,
}
_AUDIT_KEY = "k" * 40


def _use_key(monkeypatch: pytest.MonkeyPatch, key: str | None, secret: str = "s" * 32) -> None:
    settings = SimpleNamespace(
        audit_pseudonym_key=SecretStr(key) if key is not None else None,
        app_secret_key=SecretStr(secret),
    )
    monkeypatch.setattr(audit_pseudonym, "get_settings", lambda: settings)


def test_same_data_gives_same_ref_after_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_key(monkeypatch, _AUDIT_KEY)
    ref = audit_pseudonym.audit_ref

    assert ref("Ana@Empresa.com ", "email") == ref("ana@empresa.com", "email")
    assert ref("Nómina Marzo.PDF", "filename") == ref("nómina marzo.pdf", "filename")
    assert ref("Ana  García", "name") == ref("ana garcia", "name")
    assert len(ref("ana@empresa.com", "email") or "") == 32
    # Mismo texto en distinto tipo no se puede cruzar.
    assert ref("ana", "email") != ref("ana", "name")
    assert ref(None, "email") is None and ref("  ", "email") is None


def test_ref_depends_on_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_key(monkeypatch, _AUDIT_KEY)
    first = audit_pseudonym.audit_ref("ana@empresa.com", "email")
    _use_key(monkeypatch, "z" * 40)
    assert audit_pseudonym.audit_ref("ana@empresa.com", "email") != first
    # Sin clave (fuera de producción) se deriva de APP_SECRET_KEY, de forma estable.
    _use_key(monkeypatch, None)
    fallback = audit_pseudonym.audit_ref("ana@empresa.com", "email")
    assert fallback == audit_pseudonym.audit_ref("ana@empresa.com", "email")
    assert fallback not in {first, None}


def test_file_metadata_has_no_name_in_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_key(monkeypatch, _AUDIT_KEY)
    meta = audit_pseudonym.file_metadata("Nómina María García.PDF", sha256="ab" * 32)
    assert meta["file_ext"] == ".pdf"
    assert meta["file_sha256"] == "ab" * 32
    assert len(meta["filename_ref"]) == 32
    assert "María" not in str(meta) and "Nómina" not in str(meta)
    assert audit_pseudonym.file_metadata(None) == {}


# ── Configuración ────────────────────────────────────────────────────────────


def _prod(**overrides: object) -> Settings:
    values: dict[str, object] = {
        **_BASE,
        "app_env": "production",
        "audit_pseudonym_key": _AUDIT_KEY,
        "activity_log_retention_days": 90,
        "clerk_jwt_audience": "aud",
    }
    values.update(overrides)
    return Settings(**values)


def test_defaults_are_two_years_and_derived_key_outside_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUDIT_PSEUDONYM_KEY", raising=False)
    settings = Settings(**_BASE)
    assert settings.audit_log_retention_days == 730
    assert settings.audit_pseudonym_key is None


@pytest.mark.parametrize("days", [0, 1, 364])
def test_production_rejects_short_or_disabled_retention(days: int) -> None:
    with pytest.raises(ValidationError, match="AUDIT_LOG_RETENTION_DAYS"):
        _prod(audit_log_retention_days=days)


def test_production_requires_a_separate_audit_key() -> None:
    with pytest.raises(ValidationError, match="AUDIT_PSEUDONYM_KEY es obligatoria"):
        _prod(audit_pseudonym_key=None)
    with pytest.raises(ValidationError, match="distinta de APP_SECRET_KEY"):
        _prod(audit_pseudonym_key="x" * 32)
    assert _prod().audit_log_retention_days == 730


# ── Guardia: sin datos personales en claro en la metadata de auditoría ────────

_APP = Path(__file__).resolve().parents[2] / "app"
_FORBIDDEN_KEYS = frozenset(
    {"email", "filename", "original_filename", "display_name", "google_email", "phone"}
)


def _metadata_literal_keys() -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for path in _APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if name != "log_action":
                continue
            for keyword in node.keywords:
                if keyword.arg == "metadata" and isinstance(keyword.value, ast.Dict):
                    for key in keyword.value.keys:
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            found.append((path.name, node.lineno, key.value))
    return found


def test_audit_metadata_never_stores_personal_data_in_clear() -> None:
    keys = _metadata_literal_keys()
    assert keys, "la guardia no encuentra llamadas a log_action"
    offenders = [item for item in keys if item[2] in _FORBIDDEN_KEYS]
    assert offenders == [], f"usa app.core.audit_pseudonym en su lugar: {offenders}"
