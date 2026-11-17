"""Opt-in private-object-store smoke test for the PCAP capture backend.

The test writes one empty, valid classic PCAP under a random key, reads it
back, verifies an app-minted HMAC URL, and deletes it. It never puts the bucket,
object key, credentials, signing secret, or packet bytes in the evidence ledger.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit
import uuid

import pytest

from app.knowledge.storage.s3 import S3Storage
from app.telephony.pcap import S3CaptureStore, build_storage_key
from tests.real.conftest import RealEvidenceWriter, require_provider_env

pytestmark = pytest.mark.real_provider

_EMPTY_CLASSIC_RAW_PCAP = (
    b"\xd4\xc3\xb2\xa1\x02\x00\x04\x00"
    + bytes(8)
    + (65535).to_bytes(4, "little")
    + (101).to_bytes(4, "little")
)


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_sip_pcap_s3_roundtrip_signed_url_and_cleanup(
    request: pytest.FixtureRequest,
    evidence_writer: RealEvidenceWriter,
) -> None:
    env = require_provider_env("sip_gateway_s3", request=request, evidence_writer=evidence_writer)
    storage = S3Storage(
        bucket=env["VOXDESK_REAL_PCAP_S3_BUCKET"],
        region=env["VOXDESK_REAL_PCAP_S3_REGION"],
        endpoint_url=env.get("VOXDESK_REAL_PCAP_S3_ENDPOINT_URL") or None,
    )
    store = S3CaptureStore(
        secret=env["VOXDESK_REAL_PCAP_S3_SIGNING_SECRET"],
        storage=storage,
    )
    key = build_storage_key("real-integration", uuid.uuid4().hex)
    object_may_exist = False

    try:
        # Mark before issuing PUT so cleanup also covers a timeout with unknown
        # outcome, where the provider may have accepted the object.
        object_may_exist = True
        await store.put(key, _EMPTY_CLASSIC_RAW_PCAP)
        returned = await store.get(key)
        assert returned == _EMPTY_CLASSIC_RAW_PCAP

        signed = await store.signed_url(key, expires_in=60)
        query = parse_qs(urlsplit(signed).query)
        expires = int(query["expires"][0])
        signature = query["sig"][0]
        assert store.verify(key, expires=expires, signature=signature)

        assert await store.delete(key)
        object_may_exist = False
        evidence_writer.record(
            provider="sip_gateway_s3",
            status="PASS",
            test_node=request.node.nodeid,
            model="voxdesk-sip-pcap-s3-store",
            details={
                "evidence_scope": "pcap_object_store_roundtrip_signature_delete",
                "upload_readback_verified": True,
                "app_signed_url_verified": True,
                "object_delete_verified": True,
                "object_bytes": len(_EMPTY_CLASSIC_RAW_PCAP),
                "object_persisted_after_test": False,
            },
        )
    except Exception as exc:
        evidence_writer.record(
            provider="sip_gateway_s3",
            status="FAIL",
            test_node=request.node.nodeid,
            reason=f"PCAP S3 integration failed with {type(exc).__name__}",
        )
        raise
    finally:
        if object_may_exist:
            try:
                await store.delete(key)
            except Exception:
                # Preserve the primary failure; operators can sweep the random
                # `real-integration/` prefix if the provider is unavailable.
                pass
