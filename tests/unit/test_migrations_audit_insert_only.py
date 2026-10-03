"""audit_log es solo inserción para saas_app (Backlog P2c-1)."""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "versions"

# Conceden UPDATE/DELETE sobre audit_log (directo o con ON ALL TABLES) antes de
# p78_audit_insert_only_01, que lo revoca. Historial inmutable: no puede crecer.
_LEGACY_GRANTS = frozenset(
    {
        "p06_saas_app_03_create_saas_app_role.py",
        "p16_audit_01_add_audit_log.py",
    }
)

_GRANT = re.compile(r"\bGRANT\b([^;\"']*?)\bON\b([^;\"']*?)\bTO\b", re.IGNORECASE)


def _upgrade_source(path: Path) -> str:
    return path.read_text(encoding="utf-8").split("def downgrade", 1)[0]


def _grants_write_on_audit_log(source: str) -> bool:
    for privileges, target in _GRANT.findall(source):
        writes = re.search(r"\b(UPDATE|DELETE|ALL)\b", privileges, re.IGNORECASE)
        on_audit = re.search(r"\baudit_log\b|\bALL\s+TABLES\b", target, re.IGNORECASE)
        if writes and on_audit:
            return True
    return False


def test_no_new_migration_grants_write_on_audit_log() -> None:
    offenders = sorted(
        path.name
        for path in MIGRATIONS_DIR.glob("*.py")
        if path.name not in _LEGACY_GRANTS and _grants_write_on_audit_log(_upgrade_source(path))
    )
    assert offenders == [], (
        f"Migraciones que conceden UPDATE/DELETE sobre audit_log: {offenders}. "
        "audit_log es solo inserción para saas_app (p78_audit_insert_only_01)."
    )


def test_legacy_allowlist_matches_existing_files() -> None:
    missing = sorted(n for n in _LEGACY_GRANTS if not (MIGRATIONS_DIR / n).exists())
    assert missing == []


def test_detector_catches_direct_and_schema_wide_grants() -> None:
    assert _grants_write_on_audit_log("GRANT SELECT, UPDATE ON audit_log TO saas_app")
    assert _grants_write_on_audit_log("GRANT DELETE ON ALL TABLES IN SCHEMA public TO saas_app")
    assert not _grants_write_on_audit_log("GRANT SELECT, INSERT ON audit_log TO saas_app")
    assert not _grants_write_on_audit_log("GRANT SELECT, UPDATE ON invoices TO saas_app")


def test_p78_revokes_update_delete_after_p77() -> None:
    path = MIGRATIONS_DIR / "p78_audit_insert_only_01.py"
    source = path.read_text(encoding="utf-8")

    assert 'down_revision: str | None = "p77_quota_usage_01"' in source
    assert "REVOKE UPDATE, DELETE ON audit_log FROM saas_app" in _upgrade_source(path)
