"""S3 brand-asset storage remains tenant-scoped and uses the configured KMS key."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.platform_v2.errors import ValidationFailed
from app.platform_v2.whitelabel import assets

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class FakeBody:
    def __init__(self, data: bytes):
        self.data = data
        self.closed = False

    def read(self, amount: int) -> bytes:
        return self.data[:amount]

    def close(self) -> None:
        self.closed = True


class FakeS3:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_requests: list[dict] = []

    def put_object(self, **request):
        self.put_requests.append(request)
        self.objects[(request["Bucket"], request["Key"])] = request["Body"]

    def get_object(self, *, Bucket: str, Key: str):
        data = self.objects[(Bucket, Key)]
        return {"Body": FakeBody(data), "ContentLength": len(data)}

    def delete_object(self, *, Bucket: str, Key: str):
        self.objects.pop((Bucket, Key), None)


@pytest.fixture

def s3_settings(monkeypatch, tmp_path):
    from app.platform_v2 import settings as settings_mod

    settings_mod.reset_plat2_settings_cache()
    monkeypatch.setenv("BRANDING_ASSET_DIR", str(tmp_path))
    monkeypatch.setenv("BRANDING_ASSET_S3_BUCKET", "voxdesk-test-assets")
    monkeypatch.setenv("BRANDING_ASSET_S3_REGION", "ap-south-1")
    monkeypatch.setenv("BRANDING_ASSET_S3_KMS_KEY_ID", "arn:aws:kms:ap-south-1:123456789012:key/test")
    yield
    settings_mod.reset_plat2_settings_cache()


async def test_store_and_read_use_the_tenant_prefix_and_kms_encryption(monkeypatch, s3_settings):
    client = FakeS3()
    monkeypatch.setattr(assets, "_s3_client", lambda: client)
    tenant_id = uuid.uuid4()
    validated = assets.validate_upload(
        kind="logo",
        filename="logo.png",
        content_type="image/png",
        data=PNG_BYTES,
    )

    class EmptyResult:
        def scalar_one_or_none(self):
            return None

    class Session:
        def __init__(self):
            self.row = None

        async def execute(self, _statement):
            return EmptyResult()

        def add(self, row):
            self.row = row

        async def flush(self):
            return None

    session = Session()
    row, created = await assets.store(session, tenant_id=tenant_id, asset=validated)
    assert created is True
    assert row.storage_path.startswith(f"s3://voxdesk-test-assets/tenants/{tenant_id}/")
    assert len(client.put_requests) == 1
    assert client.put_requests[0]["ServerSideEncryption"] == "aws:kms"
    assert client.put_requests[0]["SSEKMSKeyId"] == (
        "arn:aws:kms:ap-south-1:123456789012:key/test"
    )
    assert await assets.read_bytes(row) == PNG_BYTES

    await assets.delete_storage_path(row.storage_path, tenant_id=tenant_id)
    assert not client.objects


async def test_s3_asset_path_cannot_cross_tenant_or_bucket(monkeypatch, s3_settings):
    client = FakeS3()
    monkeypatch.setattr(assets, "_s3_client", lambda: client)
    tenant_id = uuid.uuid4()
    asset = SimpleNamespace(
        tenant_id=tenant_id,
        storage_path=f"s3://voxdesk-test-assets/tenants/{uuid.uuid4()}/logo.png",
        size_bytes=4,
        sha256="0" * 64,
    )
    with pytest.raises(ValidationFailed, match="tenant bucket"):
        await assets.read_bytes(asset)

    outside_bucket = SimpleNamespace(
        tenant_id=tenant_id,
        storage_path=f"s3://other-bucket/tenants/{tenant_id}/logo.png",
        size_bytes=4,
        sha256="0" * 64,
    )
    with pytest.raises(ValidationFailed, match="tenant bucket"):
        await assets.read_bytes(outside_bucket)


async def test_database_delete_failure_does_not_remove_the_s3_object(monkeypatch, s3_settings):
    client = FakeS3()
    monkeypatch.setattr(assets, "_s3_client", lambda: client)
    tenant_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    storage_path = f"s3://voxdesk-test-assets/tenants/{tenant_id}/logo.png"
    client.objects[("voxdesk-test-assets", f"tenants/{tenant_id}/logo.png")] = PNG_BYTES
    row = SimpleNamespace(id=asset_id, tenant_id=tenant_id, storage_path=storage_path)

    class Result:
        def scalar_one_or_none(self):
            return row

    class Session:
        async def execute(self, _statement):
            return Result()

        async def delete(self, _row):
            return None

        async def commit(self):
            raise RuntimeError("commit failed")

        async def rollback(self):
            return None

    with pytest.raises(RuntimeError, match="commit failed"):
        await assets.delete(Session(), tenant_id=tenant_id, asset_id=asset_id, commit=True)
    assert client.objects[("voxdesk-test-assets", f"tenants/{tenant_id}/logo.png")] == PNG_BYTES
