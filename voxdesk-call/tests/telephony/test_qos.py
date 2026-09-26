"""QoS normalization. Missing stays missing. MOS is never fabricated."""

from __future__ import annotations

import pytest

from app.telephony.provider_errors import ProviderValidationError
from app.telephony.qos import aggregate, normalize, record
from app.tenancy.isolation import NotFound
from tests.conftest import make_call

pytestmark = pytest.mark.asyncio


def test_valid_metrics_and_missing_metric():
    cleaned = normalize(
        {"packet_loss": 1.5, "jitter_ms": 20, "rtt_ms": 80, "latency_ms": 40},
        provider="twilio",
    )
    assert cleaned["packet_loss"] == 1.5
    assert cleaned["jitter_ms"] == 20
    assert cleaned["rtt_ms"] == 80
    assert cleaned["quality_index"] is not None
    assert cleaned["mos"] is None
    assert "index_v1" in cleaned["quality_formula"]
    partial = normalize({"packet_loss": 1}, provider="twilio")
    assert partial["jitter_ms"] is None
    assert partial["quality_index"] is None
    assert partial["mos"] is None


def test_negative_and_unit_and_mos_are_rejected():
    with pytest.raises(ProviderValidationError):
        normalize({"packet_loss": -1}, provider="twilio")
    with pytest.raises(ProviderValidationError):
        normalize({"jitter_ms": -0.1}, provider="twilio")
    with pytest.raises(ProviderValidationError):
        normalize({"rtt_ms": -5}, provider="twilio")
    with pytest.raises(ProviderValidationError):
        normalize({"packet_loss": 1, "mos": 4.2}, provider="twilio")
    scaled = normalize(
        {"jitter_ms": 0.02, "jitter_unit": "s", "packet_loss": 0, "rtt_ms": 10}, provider="twilio"
    )
    assert scaled["jitter_ms"] == 20


async def test_aggregation_is_tenant_scoped(db, tenant_a, tenant_b):
    call_a = await make_call(db, tenant_a)
    call_b = await make_call(db, tenant_b)
    await record(
        db,
        tenant_id=tenant_a.id,
        call_id=call_a.id,
        provider="twilio",
        raw={"packet_loss": 2, "jitter_ms": 10, "rtt_ms": 100},
    )
    await record(
        db,
        tenant_id=tenant_b.id,
        call_id=call_b.id,
        provider="twilio",
        raw={"packet_loss": 50, "jitter_ms": 80, "rtt_ms": 400},
    )
    summary = await aggregate(db, tenant_a.id)
    assert summary["samples"] == 1
    assert summary["packet_loss"] == 2
    assert summary["mos"] is None
    other = await aggregate(db, tenant_b.id)
    assert other["packet_loss"] == 50
    from app.telephony.qos import get_owned

    sample = await aggregate(db, tenant_a.id)
    assert sample["tenant_id"] == str(tenant_a.id)
    with pytest.raises(NotFound):
        from app.telephony.qos import QosSample
        from sqlalchemy import select

        row = (
            await db.execute(select(QosSample).where(QosSample.tenant_id == tenant_b.id))
        ).scalar_one()
        await get_owned(db, tenant_a.id, row.id)
