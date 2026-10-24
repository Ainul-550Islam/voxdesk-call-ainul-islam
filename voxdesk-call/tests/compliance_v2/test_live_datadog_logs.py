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


# --------------------------------------------------------------------- KMS ---


async def test_live_datadog_logs_accepts_a_batch():
    env = _env("VOXDESK_LIVE_DATADOG_API_KEY")
    from app.compliance_v2.siem.sink_datadog import DatadogLogsSink

    sink = DatadogLogsSink(
        api_key=env["VOXDESK_LIVE_DATADOG_API_KEY"],
        site=os.environ.get("VOXDESK_LIVE_DATADOG_SITE", "us1"),
    )
    try:
        result = await sink.send_batch([_event()])
        assert result.accepted == 1, result.detail
        assert not result.retryable, result.message
    finally:
        await sink.aclose()


