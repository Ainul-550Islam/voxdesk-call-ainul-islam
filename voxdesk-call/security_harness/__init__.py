"""Security regression harness (Batch 03, block F).

Four suites, each answering a question a pentest report asked:

* :mod:`security_harness.authz_matrix` — is every mounted operation behind
  authentication, and is the public surface still the reviewed allowlist?
* :mod:`security_harness.cross_tenant_fuzz` — does a valid credential for tenant A
  read tenant B's objects?
* :mod:`security_harness.ssrf_probe` — do the outbound guards refuse the classic
  bypass destinations, and still accept ordinary ones?
* :mod:`security_harness.headers_and_cors` / :mod:`security_harness.rate_limit_probe` — are
  the response headers, CORS policy and throttling what they claim to be?

The suites are libraries, not scripts: :mod:`security_harness.runner` executes
them and returns one report, which is stored under ``evidence/security/`` with a
SHA-256 so an auditor can tell whether the file changed.
"""

from __future__ import annotations

from security_harness.models import (
    ERROR,
    FAIL,
    PASS,
    REVIEW,
    SKIP,
    HarnessReport,
    ProbeResult,
    worst,
)

__all__ = [
    "ERROR",
    "FAIL",
    "PASS",
    "REVIEW",
    "SKIP",
    "HarnessReport",
    "ProbeResult",
    "worst",
]
