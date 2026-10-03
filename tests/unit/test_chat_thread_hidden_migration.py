"""Guards for chat_threads.is_hidden migration (p60)."""

from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "versions"


def test_p60_chat_thread_hidden_migration_content() -> None:
    source = (MIGRATIONS_DIR / "p60_chat_thread_hidden_01.py").read_text(encoding="utf-8")
    assert 'revision: str = "p60_chat_thread_hidden_01"' in source
    assert len("p60_chat_thread_hidden_01") <= 32
    assert 'down_revision: str | None = "p59_wa_phone_unique_01"' in source
    assert "is_hidden" in source
    assert "chat_threads" in source
