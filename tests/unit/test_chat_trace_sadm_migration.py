"""Guards for SADM chat-trace superadmin_select migration (p61)."""

from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "versions"


def test_p61_chat_trace_sadm_migration_content() -> None:
    source = (MIGRATIONS_DIR / "p61_chat_trace_sadm_01.py").read_text(encoding="utf-8")
    assert 'revision: str = "p61_chat_trace_sadm_01"' in source
    assert len("p61_chat_trace_sadm_01") <= 32
    assert 'down_revision: str | None = "p60_chat_thread_hidden_01"' in source
    for table in ("chat_threads", "chat_messages", "audit_log"):
        assert table in source
    assert "superadmin_select" in source
    assert "app.superadmin_lookup" in source
    assert "FOR SELECT" in source


def test_p72_drops_superadmin_chat_read() -> None:
    """El superadmin no lee chats de otros tenants: p72 quita la política en chat."""
    source = (MIGRATIONS_DIR / "p72_drop_sadm_chat_read_01.py").read_text(encoding="utf-8")
    assert 'revision: str = "p72_drop_sadm_chat_read_01"' in source
    assert len("p72_drop_sadm_chat_read_01") <= 32
    assert 'down_revision: str | None = "p71_revoke_truncate_01"' in source
    assert '_CHAT_TABLES = ("chat_threads", "chat_messages")' in source
    assert "DROP POLICY IF EXISTS superadmin_select" in source
    # audit_log conserva su política (métrica SADM de uso de chat).
    assert "audit_log" not in source.split("def upgrade")[1].split("def downgrade")[0]


def test_no_later_migration_restores_superadmin_chat_read() -> None:
    """Ninguna migración posterior a p72 vuelve a abrir chat al superadmin."""
    for path in sorted(MIGRATIONS_DIR.glob("p*.py")):
        if path.name <= "p72_drop_sadm_chat_read_01.py":
            continue
        upgrade = (
            path.read_text(encoding="utf-8").split("def upgrade")[-1].split("def downgrade")[0]
        )
        if "superadmin_select" in upgrade:
            assert "chat_threads" not in upgrade and "chat_messages" not in upgrade, path.name
