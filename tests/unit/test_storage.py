"""Cliente R2/S3 mock con moto."""

from urllib.parse import urlparse
from uuid import uuid4

import boto3
import pytest
from app.config import get_settings
from app.core.keys import invoice_key
from app.core.storage import Storage
from moto import mock_aws


def test_invoice_key_paths() -> None:
    tenant_id = uuid4()
    key = invoice_key(tenant_id, "subdir/archivo abril.pdf")
    assert key.startswith(f"invoices/{tenant_id}/")
    assert "-" in key
    assert ".pdf" in key


@pytest.mark.asyncio
async def test_storage_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R2_ACCOUNT_ID", "test-acct")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-sk")
    monkeypatch.setenv("R2_BUCKET", "test-bucket")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://s3.amazonaws.com")
    monkeypatch.setenv("R2_REGION", "us-east-1")
    monkeypatch.setenv("STORAGE_PRESIGNED_TTL_SECONDS", "3600")
    get_settings.cache_clear()

    with mock_aws():
        admin = boto3.client("s3", region_name="us-east-1")
        admin.create_bucket(Bucket="test-bucket")

        storage = Storage()
        blob = b"hello world"
        key = "test/file.txt"

        await storage.upload_bytes(key, blob, "text/plain")
        assert await storage.exists(key)

        got = await storage.download_bytes(key)
        assert got == blob

        url = await storage.presigned_url_get(key)
        assert "test-bucket" in url
        assert urlparse(url).scheme in ("http", "https")

        await storage.delete(key)
        assert not await storage.exists(key)

    get_settings.cache_clear()
