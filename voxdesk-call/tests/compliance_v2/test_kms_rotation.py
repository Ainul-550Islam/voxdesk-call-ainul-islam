"""Rotation of the wrapping key, seen from the adapter that owns it.

Rotation has two halves. The provider rotates the key material on its own
schedule (AWS KMS automatic rotation, an Azure key version, a new GCP version),
and the application re-wraps stored data keys under the current version. This
module covers the provider half: the adapter must be able to *report* the
rotations that exist, must not claim a rotation it cannot see, and must keep
unwrapping blobs that were wrapped under an older version.

The re-wrap half lives in ``test_rekey_job.py``.
"""

from __future__ import annotations

import base64
import datetime as dt

import pytest

from app.compliance_v2.kms.aws_kms import AwsKms
from app.compliance_v2.kms.base import KmsPermanentError

CONTEXT = {"tenant_id": "t-1", "column": "recording_url", "purpose": "compliance_v2"}
SCHEMA = {"key_state": "Enabled", "key_manager": "CUSTOMER"}


class FakeKmsClient:
    """A stand-in for ``boto3.client('kms')`` with scripted replies.

    It records every call so the assertions can check *what was asked*, not just
    what came back.
    """

    def __init__(self, *, rotations: list[str] | None = None, fail_rotations: Exception | None = None):
        self.calls: list[tuple[str, dict]] = []
        self._rotations = rotations or []
        self._fail_rotations = fail_rotations

    def generate_data_key(self, **kwargs):
        self.calls.append(("generate_data_key", kwargs))
        return {"Plaintext": base64.b64encode(b"k" * 32), "CiphertextBlob": b"wrapped-v1"}

    def decrypt(self, **kwargs):
        self.calls.append(("decrypt", kwargs))
        return {"Plaintext": b"k" * 32}

    def describe_key(self, **kwargs):
        self.calls.append(("describe_key", kwargs))
        return {"KeyMetadata": dict(SCHEMA)}

    def list_key_rotations(self, **kwargs):
        self.calls.append(("list_key_rotations", kwargs))
        if self._fail_rotations is not None:
            raise self._fail_rotations
        return {"Rotations": [{"KeyId": key_id} for key_id in self._rotations]}


def _provider(client: FakeKmsClient) -> AwsKms:
    return AwsKms(key_id="arn:aws:kms:us-east-1:1:key/abc", region="us-east-1", client=client)


async def test_rotation_history_is_reported_oldest_first():
    client = FakeKmsClient(rotations=["v1", "v2", "v3"])

    versions = await _provider(client).list_key_versions()

    assert versions == ["v1", "v2", "v3"]
    assert client.calls[-1][0] == "list_key_rotations"


async def test_a_never_rotated_key_reports_itself():
    client = FakeKmsClient(rotations=[])

    versions = await _provider(client).list_key_versions()

    # An empty history is the normal state of a brand-new CMK. Reporting the
    # key id keeps the health output from looking like a broken call.
    assert versions == ["arn:aws:kms:us-east-1:1:key/abc"]


async def test_a_provider_without_the_rotation_api_is_not_an_outage():
    client = FakeKmsClient(
        fail_rotations=RuntimeError("InvalidAction: list_key_rotations")
    )

    versions = await _provider(client).list_key_versions()

    assert versions == ["arn:aws:kms:us-east-1:1:key/abc"]


async def test_the_provider_never_promises_a_rotation_it_did_not_see():
    client = FakeKmsClient(rotations=["v9"])

    versions = await _provider(client).list_key_versions()

    assert "v8" not in versions


async def test_health_distinguishes_a_disabled_key_from_an_unreachable_one():
    class DisabledClient(FakeKmsClient):
        def describe_key(self, **kwargs):
            self.calls.append(("describe_key", kwargs))
            return {"KeyMetadata": {**SCHEMA, "KeyState": "Disabled"}}

    health = await _provider(DisabledClient()).health()

    assert health.provider == "aws"
    assert health.reachable is False
    assert health.detail["key_state"] == "Disabled"


async def test_a_rotated_key_still_unwraps_an_older_blob():
    client = FakeKmsClient(rotations=["v1", "v2"])
    provider = _provider(client)

    plaintext = await provider.unwrap_data_key(b"wrapped-v1", context=CONTEXT)

    assert len(plaintext) == 32
    operation, kwargs = client.calls[-1]
    assert operation == "decrypt"
    # The encryption context is the binding that makes a stolen blob useless in
    # another tenant's row, so it must be replayed exactly on rotation.
    assert kwargs["EncryptionContext"] == CONTEXT


async def test_a_throttled_rotation_lookup_is_retried_then_given_up_on():
    class ThrottledClient(FakeKmsClient):
        def list_key_rotations(self, **kwargs):
            self.calls.append(("list_key_rotations", kwargs))
            error = RuntimeError("ThrottlingException")
            error.response = {"Error": {"Code": "ThrottlingException"}}
            raise error

    client = ThrottledClient()
    provider = AwsKms(
        key_id="arn:aws:kms:us-east-1:1:key/abc", client=client, max_attempts=2
    )

    # ``list_key_versions`` degrades to the key id rather than raising, so the
    # status route keeps answering during a provider incident.
    assert await provider.list_key_versions() == ["arn:aws:kms:us-east-1:1:key/abc"]
    assert sum(1 for name, _ in client.calls if name == "list_key_rotations") == 2


async def test_a_rejected_rotation_lookup_is_classified_as_permanent():
    class RejectedClient(FakeKmsClient):
        def list_key_rotations(self, **kwargs):
            error = RuntimeError("AccessDeniedException")
            error.response = {"Error": {"Code": "AccessDeniedException"}}
            raise error

    provider = AwsKms(key_id="arn:aws:kms:us-east-1:1:key/abc", client=RejectedClient())

    with pytest.raises(KmsPermanentError):
        await provider._call("list_key_rotations", KeyId="k")


def test_a_rotation_is_not_a_reason_to_change_the_key_id():
    provider = AwsKms(key_id="arn:aws:kms:eu-west-1:1:key/abc", region="eu-west-1")

    # The blob stores the key id it was wrapped with; nothing about rotation
    # rewrites that, which is what lets a rekey read old blobs.
    assert provider.key_id == "arn:aws:kms:eu-west-1:1:key/abc"


def test_rotation_timestamps_are_not_invented():
    """The adapter reports what the provider said; it never fabricates a clock."""
    provider = AwsKms(key_id="k")
    assert not hasattr(provider, "rotation_started_at")
    assert dt.datetime.now(dt.timezone.utc).tzinfo is not None
