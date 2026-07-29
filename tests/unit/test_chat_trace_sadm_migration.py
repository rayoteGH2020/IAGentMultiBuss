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
