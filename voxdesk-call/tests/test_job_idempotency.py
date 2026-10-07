"""Batch 07: cross-system duplicate-operation protection."""
from app.jobs.idempotency import business_key, attempt_key

def test_business_keys_stable():
    assert business_key(type("J", (), {"id":"x","job_type":"t","idempotency_key":"k","tenant_id":"t"})()) == "t:k"

def test_attempt_key_distinct():
    j = type("J", (), {"id":"x","job_type":"t","idempotency_key":"k","attempt_count":2,"tenant_id":"t"})()
    assert attempt_key(j) != business_key(j)
