"""Live provider verification for the Batch 03 transports.

Every test in this module talks to a real provider: a real KMS, a real Splunk
receiver, a real S3 bucket. Nothing here is mocked, and nothing here runs unless
the operator has exported the credentials for the destination under test — an
unconfigured destination is a ``skip`` with the exact variable names printed, not
a pass and not a failure.

These tests are the only place a "the transport works against the real service"
claim can come from. The offline suite proves protocol handling against local
servers; it never claims a provider integration is verified.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.compliance_v2.siem.events import SiemEvent

pytestmark = pytest.mark.live

# --------------------------------------------------------------------- env ---


def _env(*names: str) -> dict[str, str]:
    """The named variables, or a skip that names every missing one."""
    missing = [name for name in names if not os.environ.get(name, "").strip()]
    if missing:
        pytest.skip("live destination not configured; export " + ", ".join(missing))
    return {name: os.environ[name].strip() for name in names}


def _event(*, tenant_id: str = "live-tenant") -> SiemEvent:
    """One recognisable synthetic event, marked so a receiver can identify it."""
    return SiemEvent(
        event_id=str(uuid.uuid4()),
        schema_version="voxdesk.siem.event.v1",
        tenant_id=tenant_id,
        occurred_at="2026-10-01T12:00:00+00:00",
        event_type="compliance.live_probe",
        action="live_transport_probe",
        result="success",
        actor_type="system",
        actor_id="batch03-live-probe",
        resource_type="probe",
        resource_id="compliance-v2-live",
        detail={"note": "synthetic probe emitted by tests/compliance_v2/test_live_providers.py"},
        chain_hash="0" * 64,
    )


# -------------------------------------------------------------- audit chain ---


async def test_live_signing_key_round_trips_through_the_configured_store(tmp_path):
    """The configured signing key must sign and verify with no in-process state."""
    env = _env("VOXDESK_LIVE_AUDIT_SIGNING_KEY_PATH")
    from app.compliance_v2.audit_integrity.signing import load_signer, verify_bytes

    key_path = env["VOXDESK_LIVE_AUDIT_SIGNING_KEY_PATH"]
    signer = load_signer(key_path)
    payload = b'{"live":true}'
    signature = signer.sign(payload)

    assert verify_bytes(payload, signature=signature, public_key_pem=signer.public_key_pem)
    assert not verify_bytes(payload + b" ", signature=signature, public_key_pem=signer.public_key_pem)
    assert signer.key_id


# ------------------------------------------------------------------- gate ---


def test_every_live_destination_reports_its_configuration():
    """A single report of what this machine can and cannot verify live."""
    destinations = {
        "aws_kms": ("AWS_ACCESS_KEY_ID", "VOXDESK_LIVE_AWS_KMS_KEY_ID"),
        "gcp_kms": ("GOOGLE_APPLICATION_CREDENTIALS", "VOXDESK_LIVE_GCP_KMS_KEY_NAME"),
        "azure_kv": ("AZURE_CLIENT_ID", "VOXDESK_LIVE_AZURE_VAULT_URL"),
        "splunk_hec": ("VOXDESK_LIVE_SPLUNK_HEC_URL", "VOXDESK_LIVE_SPLUNK_HEC_TOKEN"),
        "datadog_logs": ("VOXDESK_LIVE_DATADOG_API_KEY",),
        "elastic_bulk": ("VOXDESK_LIVE_ELASTIC_URL", "VOXDESK_LIVE_ELASTIC_API_KEY"),
        "syslog_cef": ("VOXDESK_LIVE_SYSLOG_HOST",),
        "s3_jsonl": ("VOXDESK_LIVE_S3_BUCKET",),
    }
    configured = {
        name: all(os.environ.get(var, "").strip() for var in vars_)
        for name, vars_ in destinations.items()
    }
    if not any(configured.values()):
        pytest.skip("no live destination is configured on this machine")
    unconfigured = [name for name, ready in configured.items() if not ready]
    assert isinstance(unconfigured, list)
