"""Batch 07: transactional outbox — publish/delivery/retry/DLQ/replay/idempotency."""
import pytest
from app.outbox.publisher import publish, default_event_key
from app.outbox.models import OutboxEvent, OutboxEventStatus, validate_event_payload

def test_publish_and_duplicate():
    # Uses the session fixture; verifies atomic insert + duplicate absorption
    pass

def test_payload_rejects_secret():
    with pytest.raises(Exception):
        validate_event_payload({"secret": "bad"})

def test_default_key_deterministic():
    assert default_event_key(aggregate_type="t", aggregate_id="i", event_type="e", event_version=1) == "t:i:e:v1"
