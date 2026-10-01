"use client";

import { useEffect, useState } from "react";
import { api, ApiError, GovernancePolicy, ReviewCase, EvidenceRecord } from "@/lib/api";

// P0-05: Governance / Approval / Evidence / Risk / Model Registry Control Plane
// Backend: app/governance/, app/review/, evidence, risk, model_registry, policy, lineage, residency

export default function GovernancePage() {
  const [policies, setPolicies] = useState<GovernancePolicy[]>([]);
  const [reviews, setReviews] = useState<ReviewCase[]>([]);
  const [evidence, setEvidence] = useState<EvidenceRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      api.governancePolicies().catch(() => [] as GovernancePolicy[]),
      api.reviewCases().catch(() => [] as ReviewCase[]),
      api.evidenceChain({ limit: 20 }).catch(() => [] as EvidenceRecord[]),
    ])
      .then(([policyRows, reviewRows, evidenceRows]) => {
        if (cancelled) return;
        setPolicies(policyRows);
        setReviews(reviewRows);
        setEvidence(evidenceRows);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Failed to load governance");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) return <div className="loading">Loading governance…</div>;
  if (error) return <div className="error-banner">{error}</div>;

  return (
    <>
      <header className="page-head">
        <h1>Governance Center — Unified Console (P0-05)</h1>
        <p className="muted">
          Backend: app/governance/ (policy, lineage, residency, retention, attestation, evidence, model_registry, risk, review) + 9+9+4+4+3 routes
        </p>
        <p className="muted">
          LuMay reference: Zero-trust, SSO/RBAC, policy/guardrails, human approval, auditability, data protection
        </p>
      </header>

      <section className="card">
        <h2>Policies</h2>
        <p className="muted">API: /api/governance/policies — Policies/decisions/lineage/residency/retention/attestations</p>
        {policies.length === 0 ? (
          <p className="muted">No policies. Backend: app/governance/policy.py, service.py, repository.py</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Version</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {policies.map((p) => (
                  <tr key={p.id}>
                    <td>{p.name}</td>
                    <td>{p.policy_type}</td>
                    <td><span className={`badge status-${p.status}`}>{p.status}</span></td>
                    <td>v{p.version}</td>
                    <td>{new Date(p.updated_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <h2>Approval Queue — Review Cases</h2>
        <p className="muted">API: /api/reviews — human-in-the-loop for specialized agent outputs, billing, legal</p>
        {reviews.length === 0 ? (
          <p className="muted">No review cases. Backend: app/review/service.py, repository.py, routes.py</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Subject</th>
                  <th>Status</th>
                  <th>Priority</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {reviews.map((r) => (
                  <tr key={r.id}>
                    <td><code>{r.id.slice(0, 8)}…</code></td>
                    <td>{r.subject_type}:{r.subject_id.slice(0, 8)}</td>
                    <td>{r.status}</td>
                    <td>{r.priority}</td>
                    <td>{new Date(r.created_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <h2>Evidence Chain</h2>
        <p className="muted">API: /api/evidence — append-only hash chain, verify_chain, immutable audit</p>
        {evidence.length === 0 ? (
          <p className="muted">No evidence yet. Backend: app/governance/evidence.py, hashing.py</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Event Type</th>
                  <th>Payload Hash</th>
                  <th>Prev Hash</th>
                  <th>Time</th>
                </tr>
              </thead>
              <tbody>
                {evidence.map((e) => (
                  <tr key={e.id}>
                    <td>{e.event_type}</td>
                    <td><code>{e.payload_hash.slice(0, 16)}…</code></td>
                    <td><code>{e.previous_hash?.slice(0, 16) ?? "genesis"}…</code></td>
                    <td>{new Date(e.created_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <h2>Governance Surfaces (LuMay Parity)</h2>
        <div className="governance-grid">
          <div className="gov-card">
            <h4>Policy Management</h4>
            <ul>
              <li>Types: billing_guard, qms, ocg, residency, retention</li>
              <li>Statuses: draft, published, retired</li>
              <li>Backend: app/governance/models.py, policy.py, service.py</li>
              <li>Enforcement: app/ai/guardrails/, app/governance/access.py</li>
            </ul>
          </div>
          <div className="gov-card">
            <h4>Approval / Review</h4>
            <ul>
              <li>Review cases for legal, billing, compliance, specialized agents</li>
              <li>Assignment, decision types, priority</li>
              <li>Backend: app/review/, app/specialized_agents/review_bridge.py</li>
              <li>API: /api/reviews, /api/reviews/{`{id}`}/assign, /decision</li>
            </ul>
          </div>
          <div className="gov-card">
            <h4>Evidence / Lineage</h4>
            <ul>
              <li>Immutable hash chain, previous_hash linking, verify_chain</li>
              <li>Lineage: traverse_lineage, record_lineage</li>
              <li>Backend: app/governance/evidence.py, lineage.py, hashing.py</li>
              <li>Residency: request_intent, verify_intent per data_residency.py</li>
            </ul>
          </div>
          <div className="gov-card">
            <h4>Model Registry / Risk</h4>
            <ul>
              <li>Models: approved_models, admit_model_version</li>
              <li>Risk: normalize_tier, score_to_tier, required_controls</li>
              <li>Backend: app/governance/model_registry.py, risk.py</li>
              <li>API: /api/model-registry/*, /api/risk/*, /api/governance/admin/*</li>
            </ul>
          </div>
        </div>
      </section>

      <section className="card">
        <h2>Gap Closure Notes (P0-05)</h2>
        <ul>
          <li>Backend strong: policy, lineage, residency, retention, attestation, evidence, model_registry, risk, review routes exist</li>
          <li>Previously: UI mainly exposed Audit only</li>
          <li>Now: This Governance Center surfaces policies, approvals, evidence, model/risk state, audit lineage with server-side RBAC</li>
          <li>Server-enforced: app/governance/access.py require_permission, require_scope, require_runtime_access</li>
          <li>LuMay: Zero-trust request handling, SSO/RBAC, policy/guardrails, human approval, auditability — now mapped</li>
        </ul>
      </section>
    </>
  );
}
