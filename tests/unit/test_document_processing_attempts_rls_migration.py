from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "versions"


def test_original_document_processing_attempts_migration_uses_forced_rls() -> None:
    source = (MIGRATIONS_DIR / "p52_document_retry_dismiss_01.py").read_text()

    assert "ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY" in source
    assert "USING (tenant_id::text = current_setting('app.current_tenant', true))" in source
    assert "WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true))" in source


def test_repair_migration_recreates_policy_with_write_check() -> None:
    source = (MIGRATIONS_DIR / "p54_document_processing_attempts_rls_01.py").read_text()

    assert 'revision: str = "p54_doc_proc_attempts_rls_01"' in source
    assert len("p54_doc_proc_attempts_rls_01") <= 32
    assert 'down_revision: str | None = "p30_internal_scheduling_01"' in source
    assert "ALTER TABLE {TABLE_NAME} FORCE ROW LEVEL SECURITY" in source
    assert "DROP POLICY IF EXISTS {POLICY_NAME} ON {TABLE_NAME}" in source
    assert "USING (tenant_id::text = current_setting('app.current_tenant', true))" in source
    assert "WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true))" in source
    assert "GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON {TABLE_NAME} TO saas_app" in source
