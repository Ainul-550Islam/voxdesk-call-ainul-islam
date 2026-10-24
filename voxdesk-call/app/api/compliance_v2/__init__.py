"""HTTP surface of the Batch 03 compliance control plane.

Each module owns one area and is mounted by
:mod:`app.api.compliance_v2_routes`, which composes them under
``/api/tenants/{tenant_id}/compliance``:

* :mod:`siem_routes` — destinations, delivery status, dead letters, redrive;
* :mod:`audit_routes` — chain verification, signing key, signed export bundles;
* :mod:`kms_routes` — provider health, sealed-column inventory, rekey jobs;
* :mod:`residency_routes` — region registry, tenant pinning, violations;
* :mod:`evidence_routes` — control status, evidence runs, snapshot download;
* :mod:`access_review_routes` — campaigns and per-account decisions;
* :mod:`incident_routes` — incidents, timelines, breach clocks;
* :mod:`dsar_routes` — subject access and erasure requests;
* :mod:`hipaa_routes` — PHI policy, break-glass, session limits;
* :mod:`security_routes` — the pentest-fix regression harness.

Every router here resolves the tenant from the path, checks it against the
caller's scope, and requires an explicit permission; nothing in this package
holds deployment configuration — that lives in
:mod:`app.compliance_v2.settings`.
"""
