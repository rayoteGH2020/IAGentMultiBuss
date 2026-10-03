"""Guards for WhatsApp phone_number_id uniqueness migration (p59)."""

from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "versions"


def test_p59_whatsapp_phone_unique_migration_content() -> None:
    source = (MIGRATIONS_DIR / "p59_whatsapp_phone_unique_01.py").read_text(encoding="utf-8")

    assert 'revision: str = "p59_wa_phone_unique_01"' in source
    assert len("p59_wa_phone_unique_01") <= 32
    assert 'down_revision: str | None = "p58_drop_doc_types_dup_idx"' in source
    assert "uq_channel_integrations_wa_phone_active" in source
    assert "DROP INDEX IF EXISTS {_OLD_INDEX}" in source
    assert '_OLD_INDEX = "ix_channel_integrations_phone_number_id"' in source
    assert "CREATE UNIQUE INDEX" in source
    assert "channel = 'whatsapp'" in source
    assert "status = 'revoked'" in source
