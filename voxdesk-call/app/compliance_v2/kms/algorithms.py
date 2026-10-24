"""The cryptographic algorithm inventory, and the guard that keeps it honest.

Nothing in this module asserts that the deployment is FIPS validated: that is a
property of the modules the runtime loads, not of application code. What the
module does is make the *choice* of algorithm explicit and let the status route
report it, so an operator can compare it against the module they actually
deployed. A build that swapped in an unapproved primitive fails the guard rather
than shipping silently.

The three primitives VoxDesk uses, and the standard each one comes from:

* ``AES-256-GCM`` — symmetric sealing of column values (data keys, envelopes);
* ``RSA-OAEP-256`` — Azure Key Vault wrap/unwrap of a data key;
* ``AWS KMS SYMMETRIC_DEFAULT`` / ``GOOGLE_SYMMETRIC_ENCRYPTION`` — the cloud
  providers' own wrap operation, never re-implemented here;
* ``Ed25519`` — audit export signatures. Asymmetric on purpose: a verifier must
  not need the signing secret.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.compliance_v2.kms.base import KmsPermanentError

#: Primitive -> the algorithm name used by the code.
APPROVED_ALGORITHMS: dict[str, str] = {
    "symmetric_aead": "AES-256-GCM",
    "asymmetric_wrap": "RSA-OAEP-256",
    "aws_wrap": "SYMMETRIC_DEFAULT",
    "gcp_wrap": "GOOGLE_SYMMETRIC_ENCRYPTION",
    "audit_signature": "Ed25519",
}

#: Algorithm names that must never be used, with the reason the operator sees.
FORBIDDEN_ALGORITHMS: dict[str, str] = {
    "RSA1_5": "RSAES-PKCS1-v1_5 is deprecated for key wrapping (Bleichenbacher padding oracle)",
    "RSA-OAEP": "the SHA-1 default is superseded by RSA-OAEP-256",
    "AES-128-GCM": "the data keys are 256-bit; a 128-bit wrap silently weakens them",
    "MD5": "collision-broken",
    "SHA1": "collision-broken",
    "HMAC-SHA1": "superseded by SHA-256",
}


@dataclass(frozen=True)
class AlgorithmEntry:
    """One primitive, the algorithm behind it, and where it is used."""

    primitive: str
    algorithm: str
    usage: str

    def as_dict(self) -> dict[str, str]:
        return {
            "primitive": self.primitive,
            "algorithm": self.algorithm,
            "usage": self.usage,
        }


_USAGE = {
    "symmetric_aead": "column sealing: sealed values and data-key envelopes",
    "asymmetric_wrap": "Azure Key Vault wrapKey/unwrapKey",
    "aws_wrap": "AWS KMS GenerateDataKey/Decrypt",
    "gcp_wrap": "GCP KMS encrypt/decrypt",
    "audit_signature": "signed audit export bundles",
}


def inventory() -> list[AlgorithmEntry]:
    """Every primitive the compliance code uses, in a stable order."""
    return [
        AlgorithmEntry(primitive=name, algorithm=algorithm, usage=_USAGE[name])
        for name, algorithm in APPROVED_ALGORITHMS.items()
    ]


def require_approved(algorithm: str, *, primitive: str) -> str:
    """Return ``algorithm`` if it may be used for ``primitive``.

    A forbidden name is a permanent misconfiguration: retrying it cannot help,
    so the caller gets :class:`KmsPermanentError` rather than a transient one.
    """
    name = (algorithm or "").strip()
    if name in FORBIDDEN_ALGORITHMS:
        raise KmsPermanentError(
            f"{name!r} may not be used for {primitive}: {FORBIDDEN_ALGORITHMS[name]}"
        )
    approved = APPROVED_ALGORITHMS.get(primitive)
    if approved is None:
        raise KmsPermanentError(f"unknown cryptographic primitive {primitive!r}")
    if name != approved:
        raise KmsPermanentError(
            f"{primitive} must use {approved}, not {name or '(empty)'}"
        )
    return name


def status() -> dict[str, object]:
    """The inventory in the shape the ``/kms/status`` route reports."""
    return {
        "algorithms": [entry.as_dict() for entry in inventory()],
        "forbidden": sorted(FORBIDDEN_ALGORITHMS),
    }


__all__ = [
    "APPROVED_ALGORITHMS",
    "FORBIDDEN_ALGORITHMS",
    "AlgorithmEntry",
    "inventory",
    "require_approved",
    "status",
]
