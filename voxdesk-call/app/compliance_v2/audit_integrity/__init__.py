"""Tamper-evident audit: chain verification, signing and export bundles.

The three modules are independent — verification does not need a signing key,
and building a bundle does not read the database — which is what lets the
offline verifier run against a downloaded ZIP with nothing but a public key.
"""

from __future__ import annotations

from app.compliance_v2.audit_integrity.chain_verifier import (
    ChainVerificationReport,
    verify_chain,
)
from app.compliance_v2.audit_integrity.export_bundle import (
    BundleRequest,
    BundleVerification,
    build_bundle,
    verify_bundle,
    verify_bundle_bytes,
)
from app.compliance_v2.audit_integrity.signing import (
    Signer,
    load_signer,
    sign_bytes,
    verify_bytes,
)

__all__ = [
    "BundleRequest",
    "BundleVerification",
    "ChainVerificationReport",
    "Signer",
    "build_bundle",
    "load_signer",
    "sign_bytes",
    "verify_bundle",
    "verify_bundle_bytes",
    "verify_bytes",
    "verify_chain",
]
