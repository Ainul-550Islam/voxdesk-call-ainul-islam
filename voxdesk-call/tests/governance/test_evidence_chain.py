"""Canonical evidence hashing and tamper detection tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.governance.evidence import verify_event_integrity
from app.governance.exceptions import EvidenceIntegrityError
from app.governance.hashing import (
    calculate_chain_hash,
    canonicalize_payload,
    hash_payload,
)


def test_canonical_payload_and_chain_hash_are_deterministic():
    assert canonicalize_payload({"z": 1, "a": 2}) == '{"a":2,"z":1}'
    payload_hash = hash_payload({"a": 2, "z": 1})
    assert payload_hash == hash_payload({"z": 1, "a": 2})
    assert calculate_chain_hash(None, payload_hash, {"sequence": 1}) == calculate_chain_hash(
        None, payload_hash, {"sequence": 1}
    )


def test_event_payload_tampering_is_not_valid():
    from app.governance.hashing import chain_hash

    payload = {"decision": "allow"}
    payload_hash = hash_payload(payload)
    row = SimpleNamespace(
        previous_hash=None,
        payload=payload,
        payload_hash=payload_hash,
        actor_id=None,
        actor_type="system",
        correlation_id=None,
        subject_type=None,
        subject_id=None,
        event_type="policy_decision",
        sequence=1,
        event_hash=chain_hash(
            previous_hash=None,
            payload={
                "actor_id": None,
                "actor_type": "system",
                "correlation_id": None,
                "subject_type": None,
                "subject_id": None,
                "payload_hash": payload_hash,
                "payload": payload,
            },
            event_type="policy_decision",
            sequence=1,
        ),
    )
    assert verify_event_integrity(row) is True
    row.payload["decision"] = "deny"
    with pytest.raises(EvidenceIntegrityError):
        verify_event_integrity(row)
