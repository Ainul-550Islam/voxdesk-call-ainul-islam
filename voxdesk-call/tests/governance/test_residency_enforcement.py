"""Residency intent remains distinct from physical residency proof."""

from __future__ import annotations

import pytest

from app.tenancy.data_residency import evaluate, require_physical_proof
from app.tenancy.exceptions import ResidencyDenied


def test_supported_label_still_cannot_prove_physical_residency():
    decision = evaluate("eu-central-1", catalog=frozenset({"eu-central-1"}))
    assert decision.supported is True
    assert decision.applied is False
    assert decision.physical_residency_proven is False
    with pytest.raises(ResidencyDenied):
        require_physical_proof(decision)


def test_restricted_region_is_denied():
    decision = evaluate(
        "eu-central-1",
        catalog=frozenset({"eu-central-1"}),
        restricted=frozenset({"eu-central-1"}),
    )
    assert decision.restricted is True
    assert decision.supported is False
