"""saas_app no debe recibir TRUNCATE: salta RLS y permite vaciar tablas de todos los tenants."""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "versions"

# Migraciones ya aplicadas que concedían TRUNCATE; p71_revoke_truncate_01 lo revoca.
# No se editan (el historial de Alembic es inmutable), pero no pueden crecer.
_LEGACY_TRUNCATE_GRANTS = frozenset(
    {
        "p06_grant_truncate_04_grant_truncate_saas_app.py",
        "p06_saas_app_03_create_saas_app_role.py",
        "p09_invoices_01_add_invoices_tables.py",
        "p10_llm_calls_01_add_llm_calls.py",
        "p11_doc_types_tickets_01_add_doc_types_and_tickets.py",
        "p16_audit_01_add_audit_log.py",
        "p16_chat_01_add_chat_tables_and_extensions.py",
        "p17_calendar_01_add_calendar_integrations.py",
        "p18_knowledge_01_add_knowledge_tables.py",
        "p20_usage_meter_01_add_usage_meter.py",
        "p21_c2_conversations_01.py",
        "p21_c_channel_integrations_01.py",
        "p21_e_channel_response_cache_01.py",
        "p30_internal_scheduling_01.py",
        "p54_document_processing_attempts_rls_01.py",
        "p56_document_limits_charges_01.py",
        "p62_contracts_insurances_01.py",
        "p64_plans_entitlements_01.py",
        "p65_stripe_billing_01.py",
    }
)

_GRANT_TRUNCATE = re.compile(r"GRANT\b[^;\"']*\bTRUNCATE\b", re.IGNORECASE)


def _upgrade_source(path: Path) -> str:
    """Solo el cuerpo de upgrade(): el downgrade de p71 re-concede a propósito."""
    source = path.read_text(encoding="utf-8")
    return source.split("def downgrade", 1)[0]


def test_no_new_migration_grants_truncate() -> None:
    offenders = sorted(
        path.name
        for path in MIGRATIONS_DIR.glob("*.py")
        if path.name not in _LEGACY_TRUNCATE_GRANTS
        and _GRANT_TRUNCATE.search(_upgrade_source(path))
    )
    assert offenders == [], (
        f"Migraciones que conceden TRUNCATE (salta RLS): {offenders}. "
        "Usa GRANT SELECT, INSERT, UPDATE, DELETE."
    )


def test_legacy_allowlist_matches_existing_files() -> None:
    missing = sorted(n for n in _LEGACY_TRUNCATE_GRANTS if not (MIGRATIONS_DIR / n).exists())
    assert missing == []


def test_revoke_migration_follows_p70() -> None:
    source = (MIGRATIONS_DIR / "p71_revoke_truncate_01.py").read_text(encoding="utf-8")
    upgrade = _upgrade_source(MIGRATIONS_DIR / "p71_revoke_truncate_01.py")

    assert 'down_revision: str | None = "p70_plan_ui_names_01"' in source
    assert "REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM saas_app" in upgrade
