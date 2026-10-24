"""The algorithm inventory and the guard that refuses forbidden primitives.

This does not claim FIPS validation — that comes from the modules the runtime
loads. It proves the application only ever names approved algorithms, refuses the
deprecated ones explicitly, and reports the inventory on ``GET /kms/status`` so
an operator can check it against the deployed module.
"""

from __future__ import annotations

import pytest

from app.compliance_v2.kms import algorithms
from app.compliance_v2.kms.azure_keyvault import ALGORITHM as AZURE_ALGORITHM
from app.compliance_v2.kms.base import KmsPermanentError
from tests.conftest import auth_headers


def test_every_primitive_has_an_approved_algorithm():
    entries = algorithms.inventory()

    assert {entry.primitive for entry in entries} == set(algorithms.APPROVED_ALGORITHMS)
    assert all(entry.algorithm and entry.usage for entry in entries)


def test_no_approved_algorithm_is_also_forbidden():
    assert not set(algorithms.APPROVED_ALGORITHMS.values()) & set(
        algorithms.FORBIDDEN_ALGORITHMS
    )


def test_the_azure_wrap_algorithm_is_the_pinned_one():
    assert AZURE_ALGORITHM == "RSA-OAEP-256"
    assert algorithms.require_approved(AZURE_ALGORITHM, primitive="asymmetric_wrap")


@pytest.mark.parametrize(
    ("algorithm", "fragment"),
    [
        ("RSA1_5", "PKCS1"),
        ("RSA-OAEP", "SHA-1"),
        ("AES-128-GCM", "256-bit"),
        ("MD5", "collision"),
        ("HMAC-SHA1", "SHA-256"),
    ],
)
def test_forbidden_algorithms_are_refused_with_a_reason(algorithm, fragment):
    with pytest.raises(KmsPermanentError) as raised:
        algorithms.require_approved(algorithm, primitive="asymmetric_wrap")

    assert fragment in str(raised.value)


def test_a_swap_to_an_unlisted_algorithm_is_refused():
    with pytest.raises(KmsPermanentError):
        algorithms.require_approved("AES-192-GCM", primitive="symmetric_aead")


def test_an_unknown_primitive_is_refused():
    with pytest.raises(KmsPermanentError):
        algorithms.require_approved("AES-256-GCM", primitive="not_a_primitive")


def test_the_reported_status_lists_the_inventory_and_the_denylist():
    payload = algorithms.status()

    assert {entry["primitive"] for entry in payload["algorithms"]} == set(
        algorithms.APPROVED_ALGORITHMS
    )
    assert "RSA1_5" in payload["forbidden"]


async def test_the_status_route_reports_the_policy(client, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    response = await client.get(
        f"/api/tenants/{tenant_a.id}/compliance/kms/status", headers=headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["algorithm_policy"]["algorithms"]
    assert "symmetric_aead" in {
        entry["primitive"] for entry in body["algorithm_policy"]["algorithms"]
    }
